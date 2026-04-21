import sys
import os
import time
from .base_controller import BaseController


def get_os_controller() -> BaseController:
    """Factory function to instantiate the correct Bluetooth controller based on OS and user choice."""

    platform = sys.platform

    # Dynamically label the Native option based on the true OS capabilities
    if platform.startswith('linux'):
        native_label = "Native OS Bluetooth (Direct Kernel HFP via AF_BLUETOOTH)"
    elif platform == 'darwin':
        native_label = "Native OS Bluetooth (Acoustic Bypass via PyAudio)"
    elif platform == 'win32':
        native_label = "Native OS Bluetooth (WIP / Not Yet Implemented)"
    else:
        native_label = f"Native OS Bluetooth ({platform})"

    print("\n\033[1;36m" + "=" * 40 + "\033[0m")
    print("\033[1;36m      EIDOLON CONNECTION MANAGER\033[0m")
    print("\033[1;36m" + "=" * 40 + "\033[0m\n")

    print("Select Bluetooth Architecture:")
    print(f" [1] {native_label}")
    print(" [2] ESP32 Wi-Fi Audio Bridge (Hardware Delegate)")

    choice = ""
    while choice not in ['1', '2']:
        choice = input("\nEnter your choice [1/2]: ").strip()

    if choice == '1':
        print(f"\n\033[93m[*] Booting Native OS Bluetooth Controller ({platform})...\033[0m")
        time.sleep(1)

        if platform.startswith('linux'):
            from .linux_controller import LinuxController
            return LinuxController()

        elif platform == 'darwin':
            from .mac_controller import MacController
            return MacController()

        elif platform == 'win32':
            # Graceful fallback since the Windows controller is pending
            print("\033[91m[!] Windows Native Controller is not yet designed.\033[0m")
            print("\033[93m[*] Automatically falling back to ESP32 Wi-Fi Bridge...\033[0m")
            time.sleep(2)
            from .esp32_controller import ESP32Controller
            return ESP32Controller(host='0.0.0.0', audio_port=8000, ctrl_port=8001)

        else:
            raise OSError(f"Unsupported operating system: {platform}")

    else:
        print("\n\033[96m[*] Routing Bluetooth stack through ESP32 Wi-Fi Bridge...\033[0m")
        from .esp32_controller import ESP32Controller
        return ESP32Controller(host='0.0.0.0', audio_port=8000, ctrl_port=8001)