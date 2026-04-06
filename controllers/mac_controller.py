import time
import threading
import objc

import Foundation
import IOBluetooth

from .base_controller import BaseController

DEBUG = True

NSMutableDictionary = objc.lookUpClass("NSMutableDictionary")
NSMutableArray = objc.lookUpClass("NSMutableArray")
NSNumber = objc.lookUpClass("NSNumber")
NSRunLoop = objc.lookUpClass("NSRunLoop")
NSDate = objc.lookUpClass("NSDate")

IOBluetoothSDPUUID = objc.lookUpClass("IOBluetoothSDPUUID")
IOBluetoothSDPServiceRecord = objc.lookUpClass("IOBluetoothSDPServiceRecord")
IOBluetoothDevice = objc.lookUpClass("IOBluetoothDevice")


class HFPDelegate(Foundation.NSObject):
    """Objective-C Delegate to handle active RFCOMM connection."""

    def rfcommChannelData_data_length_(self, channel, dataPointer, dataLength):
        # We read the raw memory buffer from the pointer
        data_bytes = objc.stringForBuffer(dataPointer, dataLength)
        print(f"\n\r\x1b[K[DEBUG - MacController]: \033[96mPhone replied: {repr(data_bytes)}\033[0m")
        print("Type response > ", end="", flush=True)

    def rfcommChannelClosed_(self, channel):
        print("\n\r\x1b[K[DEBUG - MacController]: \033[91mRFCOMM Connection Closed by phone.\033[0m")
        print("Type response > ", end="", flush=True)


class MacController(BaseController):
    def __init__(self):
        self.connected = False
        self.published_record = None
        self.rfcomm_channel = None
        self.delegate = None
        self.run_loop_thread = None
        self.hunt_thread = None

    def _debug_print(self, msg):
        if DEBUG:
            print(f"\r\x1b[K[DEBUG - MacController]: {msg}")
            print("Type response > ", end="", flush=True)

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

    def _start_nsrunloop(self):
        run_loop = NSRunLoop.currentRunLoop()
        while self.connected:
            run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))

    def _hunt_for_phone(self):
        time.sleep(2)
        self._debug_print("Hunting for paired phones (Audio Gateways)...")

        paired_devices = IOBluetoothDevice.pairedDevices()
        if not paired_devices:
            self._debug_print("No paired Bluetooth devices found on this Mac.")
            return

        ag_uuid = IOBluetoothSDPUUID.uuid16_(0x111F)  # Audio Gateway Profile

        for device in paired_devices:
            record = device.getServiceRecordForUUID_(ag_uuid)

            if record:
                self._debug_print(f"Found Phone: {device.name()}")

                if not device.isConnected():
                    self._debug_print(f"Waking up {device.name()}...")
                    device.openConnection()
                    time.sleep(2)

                    # Safe SDP Parsing to find RFCOMM Channel ID
                channel_id = None
                try:
                    attrs = record.attributes()
                    if attrs:
                        proto_list = attrs.objectForKey_(4)  # 4 = ProtocolDescriptorList
                        if proto_list:
                            for protocol_seq in proto_list.value():
                                items = protocol_seq.value()
                                if len(items) >= 2:
                                    uuid_str = str(items[0].value())
                                    if "0003" in uuid_str:  # 0003 is the RFCOMM UUID
                                        channel_id = int(items[1].value())
                                        break
                except Exception as e:
                    self._debug_print(f"SDP Parse Error: {e}")

                # Fallback to standard Audio Gateway ports if parsing fails
                channel_ids_to_try = [channel_id] if channel_id else [1, 2, 3]

                connected = False
                for cid in channel_ids_to_try:
                    self._debug_print(f"Attempting connection on RFCOMM Channel {cid}...")
                    self.delegate = HFPDelegate.alloc().init()
                    status, self.rfcomm_channel = device.openRFCOMMChannelSync_withChannelID_delegate_(None, cid,
                                                                                                       self.delegate)

                    if status == 0 and self.rfcomm_channel:
                        self._debug_print(f"\033[92mSUCCESS! Connected to {device.name()} on Channel {cid}!\033[0m")
                        connected = True
                        break
                    else:
                        self._debug_print(f"Failed on Channel {cid}. (Status: {status})")

                if connected:
                    # Send the HFP "Ping" (AT Command) to prove the pipeline is open
                    time.sleep(1)
                    self._debug_print("Sending AT ping to phone...")
                    self.rfcomm_channel.writeSync_length_(b"AT\r", 3)
                    return

        self._debug_print("No compatible phones found. Please ensure your phone is paired to the Mac.")

    def start_hfp_server(self) -> bool:
        try:
            sdp_dict = self._create_hfp_sdp_dictionary()
            self.published_record = IOBluetoothSDPServiceRecord.publishedServiceRecordWithDictionary_(sdp_dict)
            self.connected = True

            self.run_loop_thread = threading.Thread(target=self._start_nsrunloop, daemon=True)
            self.run_loop_thread.start()

            self.hunt_thread = threading.Thread(target=self._hunt_for_phone, daemon=True)
            self.hunt_thread.start()

            return True

        except Exception as e:
            self._debug_print(f"Exception during start: {e}")
            return False

    def read_audio(self, chunk_size: int) -> bytes:
        time.sleep(0.1)
        return b'\x00' * chunk_size

    def write_audio(self, audio_bytes: bytes):
        pass

    def stop_server(self):
        self.connected = False
        if self.rfcomm_channel:
            self.rfcomm_channel.closeChannel()
        if self.published_record:
            self.published_record.removeServiceRecord()