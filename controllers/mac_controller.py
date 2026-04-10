import time
import threading
import queue
import atexit
import ctypes
import re
import pyaudio
import objc
import Foundation

from .base_controller import BaseController

DEBUG = True

NSMutableDictionary = objc.lookUpClass("NSMutableDictionary")
NSMutableArray = objc.lookUpClass("NSMutableArray")
NSNumber = objc.lookUpClass("NSNumber")
NSRunLoop = objc.lookUpClass("NSRunLoop")
NSDate = objc.lookUpClass("NSDate")
NSPort = objc.lookUpClass("NSPort")

IOBluetoothSDPUUID = objc.lookUpClass("IOBluetoothSDPUUID")
IOBluetoothSDPServiceRecord = objc.lookUpClass("IOBluetoothSDPServiceRecord")
IOBluetoothDevice = objc.lookUpClass("IOBluetoothDevice")

# ==========================================
# OBJECTIVE-C DELEGATES
# ==========================================

class HFPDelegate(Foundation.NSObject):
    controller = None

    def rfcommChannelClosed_(self, channel):
        if self.controller:
            self.controller.channel_is_open = False
        print("[DELEGATE] RFCOMM Connection Closed by phone/baseband.")

    rfcommChannelClosed_ = objc.selector(
        rfcommChannelClosed_,
        signature=b"v@:@"
    )

    def rfcommChannelData_data_length_(self, channel, dataPointer, dataLength):
        if not self.controller: return

        try:
            if isinstance(dataPointer, bytes):
                raw_data = dataPointer[:dataLength]
            elif isinstance(dataPointer, int):
                raw_data = ctypes.string_at(dataPointer, dataLength)
            elif hasattr(dataPointer, '__int__'):
                raw_data = ctypes.string_at(int(dataPointer), dataLength)
            else:
                raw_data = bytes(dataPointer)[:dataLength]

            data_str = raw_data.decode('utf-8', errors='ignore').strip()

            if DEBUG and data_str:
                print(f"[Phone Replies]: {data_str}")

            if len(data_str) > 0:
                self.controller.probe_replied = True

            # --- THE AT HANDSHAKE STATE MACHINE ---
            if "OK" in data_str or "ERROR" in data_str:
                state = self.controller.at_state

                if state == 1:
                    # Advertise support for CVSD and mSBC Codecs
                    self.controller.send_at_command(b"AT+BAC=1,2\r")
                    self.controller.at_state = 2
                elif state == 2:
                    self.controller.send_at_command(b"AT+CIND=?\r")
                    self.controller.at_state = 3
                elif state == 3:
                    self.controller.send_at_command(b"AT+CIND?\r")
                    self.controller.at_state = 4
                elif state == 4:
                    self.controller.send_at_command(b"AT+CMER=3,0,0,1\r")
                    self.controller.at_state = 5
                elif state == 5:
                    self.controller.send_at_command(b"AT+CHLD=?\r")
                    self.controller.at_state = 6
                elif state == 6:
                    print("[State] SLC Established! Mac is now an active HFP Call Controller.")
                    self.controller.at_state = 7

            # --- CALL STATE PARSING ---
            if "RING" in data_str or "+CIEV: 2,1" in data_str:
                self.controller.event_queue.put("RING")
            elif "+CIEV: 1,1" in data_str:
                self.controller.event_queue.put("CALL_ACTIVE")
            elif "+CIEV: 1,0" in data_str:
                self.controller.event_queue.put("CALL_ENDED")
            elif "+CIEV: 2,0" in data_str:
                self.controller.event_queue.put("CALL_SETUP_ENDED")

            # --- CODEC NEGOTIATION (Handled, but Audio is Ignored by macOS Kernel) ---
            if "+BCS: 1" in data_str:
                self.controller.send_at_command(b"AT+BCS=1\r")
            elif "+BCS: 2" in data_str:
                self.controller.send_at_command(b"AT+BCS=2\r")

        except Exception as e:
            print(f"\n[Delegate Error]: {e}")

    rfcommChannelData_data_length_ = objc.selector(
        rfcommChannelData_data_length_,
        signature=b"v@:@^vQ"
    )

# ==========================================
# MAC CONTROLLER
# ==========================================

class MacController(BaseController):
    def __init__(self):
        super().__init__()
        self.connected = False
        self.published_record = None
        self.device = None
        self.rfcomm_channel = None
        self.rfcomm_delegate = None
        self.bt_worker_thread = None
        self.at_state = 0
        self.rx_audio_buffer = queue.Queue()
        self.probe_replied = False
        self.channel_is_open = False
        self.pyaudio_instance = pyaudio.PyAudio()
        self.mic_stream = None

        atexit.register(self.stop_server)

    def _debug_print(self, msg):
        if DEBUG:
            print(f"[DEBUG - MacController]: {msg}")

    def _pump_runloop(self, duration_seconds):
        run_loop = NSRunLoop.currentRunLoop()
        end_time = time.time() + duration_seconds
        while time.time() < end_time:
            run_loop.runMode_beforeDate_(Foundation.NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.1))

    def send_at_command(self, cmd_bytes: bytes):
        if self.rfcomm_channel and self.channel_is_open:
            self._debug_print(f"Sending: {repr(cmd_bytes)}")
            self.rfcomm_channel.writeSync_length_(cmd_bytes, len(cmd_bytes))

    def open_sco_channel(self):
        """Fulfills the Acoustic Bypass: Opens the Mac Mic to feed the OS-Agnostic STT pipeline."""
        if not self.mic_stream:
            self._debug_print("\033[95m[Acoustic Bypass] Opening Mac Mic stream for STT ingest...\033[0m")
            try:
                self.mic_stream = self.pyaudio_instance.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=1024
                )
            except Exception as e:
                self._debug_print(f"Failed to open Mac Mic: {e}")


    def _create_hfp_sdp_dictionary(self):
        sdp_dict = NSMutableDictionary.dictionary()
        class_id_list = NSMutableArray.array()
        class_id_list.addObject_(IOBluetoothSDPUUID.uuid16_(0x111E))
        class_id_list.addObject_(IOBluetoothSDPUUID.uuid16_(0x1203))
        sdp_dict.setObject_forKey_(class_id_list, "0001")
        protocol_list = NSMutableArray.array()
        l2cap_desc = NSMutableArray.array()
        l2cap_desc.addObject_(IOBluetoothSDPUUID.uuid16_(0x0100))
        protocol_list.addObject_(l2cap_desc)
        rfcomm_desc = NSMutableArray.array()
        rfcomm_desc.addObject_(IOBluetoothSDPUUID.uuid16_(0x0003))
        rfcomm_desc.addObject_(NSNumber.numberWithInt_(11))
        protocol_list.addObject_(rfcomm_desc)
        sdp_dict.setObject_forKey_(protocol_list, "0004")
        profile_list = NSMutableArray.array()
        profile_desc = NSMutableArray.array()
        profile_desc.addObject_(IOBluetoothSDPUUID.uuid16_(0x111E))
        profile_desc.addObject_(NSNumber.numberWithInt_(0x0107))
        profile_list.addObject_(profile_desc)
        sdp_dict.setObject_forKey_(profile_list, "0009")
        sdp_dict.setObject_forKey_("Eidolon HFP Gateway", "0100")
        sdp_dict.setObject_forKey_(NSNumber.numberWithInt_(0x001F), "0311")
        return sdp_dict

    def _extract_rfcomm_channel(self, sdp_record):
        try:
            dump = str(sdp_record.attributes())
            match = re.search(r'uuid(?:16|32)\(.*?\s03\)[^}]*?u?int\d+\((\d+)\)', dump, re.IGNORECASE)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return None

    def _bluetooth_worker(self):
        run_loop = NSRunLoop.currentRunLoop()
        dummy_port = NSPort.port()
        run_loop.addPort_forMode_(dummy_port, Foundation.NSDefaultRunLoopMode)

        ag_uuid = IOBluetoothSDPUUID.uuid16_(0x111F)
        connection_success = False

        while self.connected and not connection_success:
            self._debug_print("Hunting for paired phones (Audio Gateways)...")
            paired_devices = IOBluetoothDevice.pairedDevices()

            if paired_devices:
                processed_macs = set()
                for device in paired_devices:
                    if connection_success: break

                    addr = device.addressString()
                    if addr in processed_macs: continue
                    processed_macs.add(addr)

                    ag_record = device.getServiceRecordForUUID_(ag_uuid)

                    if ag_record:
                        self.device = device
                        self._debug_print(f"Found AG Profile on: {device.name()} ({addr})")

                        if not device.isConnected():
                            self._debug_print(f"Waking up {device.name()}...")
                            device.openConnection()

                        self._pump_runloop(2.0)
                        channel_id = self._extract_rfcomm_channel(ag_record)
                        channels_to_probe = [channel_id] if channel_id else[1, 2, 3, 4, 10, 11, 12, 13, 14]

                        for cid in channels_to_probe:
                            self._debug_print(f"Connecting to RFCOMM Port {cid}...")
                            self.rfcomm_delegate = HFPDelegate.alloc().init()
                            self.rfcomm_delegate.controller = self
                            self.probe_replied = False
                            self.channel_is_open = False

                            status = -1
                            retries = 3
                            while retries > 0:
                                status, self.rfcomm_channel = device.openRFCOMMChannelSync_withChannelID_delegate_(
                                    None, cid, self.rfcomm_delegate)
                                if status == 0 and self.rfcomm_channel:
                                    break
                                self._pump_runloop(2.0)
                                retries -= 1

                            if status == 0 and self.rfcomm_channel:
                                wait_timeout = time.time() + 4.0
                                while time.time() < wait_timeout and not self.rfcomm_channel.isOpen():
                                    self._pump_runloop(0.1)

                                if self.rfcomm_channel.isOpen():
                                    self._debug_print(f"\033[92mPort {cid} is genuinely OPEN!\033[0m")
                                    self.channel_is_open = True
                                    self._pump_runloop(1.5)

                                    if not self.channel_is_open: continue

                                    self.at_state = 1
                                    # Request Codec Negotiation feature (115 + 128 = 243)
                                    self.send_at_command(b"AT+BRSF=243\r")

                                    wait_time = 0.0
                                    while wait_time < 4.0 and not self.probe_replied and self.channel_is_open:
                                        self._pump_runloop(0.1)
                                        wait_time += 0.1

                                    if self.probe_replied:
                                        self._debug_print(f"\033[92mHFP Daemon fully engaged on Port {cid}!\033[0m")
                                        slc_timeout = time.time() + 5.0
                                        while time.time() < slc_timeout and self.at_state < 7 and self.channel_is_open:
                                            self._pump_runloop(0.1)

                                        if self.at_state >= 7:
                                            connection_success = True
                                            break
                                    else:
                                        self._debug_print(f"Port {cid} silent/closed. Cleaning up...")
                                        self.rfcomm_channel.closeChannel()
                                        self.rfcomm_channel = None
                                        self.sco_channel = None
                                        self.channel_is_open = False
                                else:
                                    self._debug_print(f"Port {cid} baseband rejected the connection.")

                        if connection_success:
                            break

            if not connection_success:
                self._debug_print("Retrying in 4 seconds...")
                self._pump_runloop(4.0)

        if connection_success:
            while self.connected:
                self._pump_runloop(0.1)

    def start_hfp_server(self) -> bool:
        try:
            sdp_dict = self._create_hfp_sdp_dictionary()
            self.published_record = IOBluetoothSDPServiceRecord.publishedServiceRecordWithDictionary_(sdp_dict)
            self.connected = True
            self.bt_worker_thread = threading.Thread(target=self._bluetooth_worker, daemon=True)
            self.bt_worker_thread.start()
            return True
        except Exception as e:
            self._debug_print(f"Exception during start: {e}")
            return False

    def read_audio(self, chunk_size: int) -> bytes:
        """Returns raw PCM bytes from the Mac Mic, pretending to be the Bluetooth SCO stream."""
        if self.mic_stream and self.mic_stream.is_active():
            try:
                return self.mic_stream.read(chunk_size, exception_on_overflow=False)
            except Exception:
                pass
        return b''

    def write_audio(self, audio_bytes: bytes):
        if self.sco_channel:
            self.sco_channel.writeSync_length_(audio_bytes, len(audio_bytes))

    def stop_server(self):
        self.connected = False
        self.channel_is_open = False

        if getattr(self, 'rfcomm_channel', None):
            self.rfcomm_channel.closeChannel()

        if getattr(self, 'published_record', None):
            self.published_record.removeServiceRecord()
            self.published_record = None

        if self.mic_stream:
            self.mic_stream.stop_stream()
            self.mic_stream.close()
        if hasattr(self, 'pyaudio_instance'):
            self.pyaudio_instance.terminate()