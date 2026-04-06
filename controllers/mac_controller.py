import time
import threading
import logging
import objc

# Import the base Apple frameworks
import Foundation
import IOBluetooth

from .base_controller import BaseController

# Dynamically look up Objective-C classes to bypass IDE linter errors
NSMutableDictionary = objc.lookUpClass("NSMutableDictionary")
NSMutableArray = objc.lookUpClass("NSMutableArray")
NSNumber = objc.lookUpClass("NSNumber")
NSRunLoop = objc.lookUpClass("NSRunLoop")
NSDate = objc.lookUpClass("NSDate")

IOBluetoothSDPUUID = objc.lookUpClass("IOBluetoothSDPUUID")
IOBluetoothSDPServiceRecord = objc.lookUpClass("IOBluetoothSDPServiceRecord")


class MacController(BaseController):
    def __init__(self):
        self.connected = False
        self.published_record = None
        self.rfcomm_channel_id = None
        self.run_loop_thread = None

    def _create_hfp_sdp_dictionary(self):
        """Creates the raw Objective-C dictionary required to spoof an HFP device."""
        sdp_dict = NSMutableDictionary.dictionary()

        # 1. Service Class ID List (Handsfree & Generic Audio)
        class_id_list = NSMutableArray.array()
        class_id_list.addObject_(IOBluetoothSDPUUID.uuid16_(0x111E))  # Handsfree
        class_id_list.addObject_(IOBluetoothSDPUUID.uuid16_(0x1203))  # Generic Audio
        sdp_dict.setObject_forKey_(class_id_list, "0001")  # ServiceClassIDList

        # 2. Protocol Descriptor List (L2CAP -> RFCOMM)
        protocol_list = NSMutableArray.array()

        l2cap_desc = NSMutableArray.array()
        l2cap_desc.addObject_(IOBluetoothSDPUUID.uuid16_(0x0100))  # L2CAP
        protocol_list.addObject_(l2cap_desc)

        rfcomm_desc = NSMutableArray.array()
        rfcomm_desc.addObject_(IOBluetoothSDPUUID.uuid16_(0x0003))  # RFCOMM
        # We assign RFCOMM Channel 7 (Arbitrary free channel)
        self.rfcomm_channel_id = 7
        rfcomm_desc.addObject_(NSNumber.numberWithInt_(self.rfcomm_channel_id))
        protocol_list.addObject_(rfcomm_desc)

        sdp_dict.setObject_forKey_(protocol_list, "0004")  # ProtocolDescriptorList

        # 3. Service Name
        sdp_dict.setObject_forKey_("Eidolon HFP Gateway", "0100")  # ServiceName

        return sdp_dict

    def _start_nsrunloop(self):
        """macOS requires a native event loop to keep the Bluetooth service alive."""
        run_loop = NSRunLoop.currentRunLoop()
        while self.connected:
            run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))

    def start_hfp_server(self) -> bool:
        try:
            # Create the SDP Dictionary
            sdp_dict = self._create_hfp_sdp_dictionary()

            # Publish it to the macOS Bluetooth Daemon
            self.published_record = IOBluetoothSDPServiceRecord.publishedServiceRecordWithDictionary_(sdp_dict)

            if self.published_record:
                self.connected = True

                # Start the Apple NSRunLoop in a background thread to keep the service broadcasting
                self.run_loop_thread = threading.Thread(target=self._start_nsrunloop, daemon=True)
                self.run_loop_thread.start()

                return True
            else:
                return False

        except Exception as e:
            return False

    def read_audio(self, chunk_size: int) -> bytes:
        # Returning silence to keep the STT pipeline alive while we test
        time.sleep(0.1)
        return b'\x00' * chunk_size

    def write_audio(self, audio_bytes: bytes):
        pass

    def stop_server(self):
        self.connected = False
        if self.published_record:
            self.published_record.removeServiceRecord()
            self.published_record = None