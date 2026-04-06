import sys
from .linux_controller import LinuxController
from .mac_controller import MacController
from .win_controller import WindowsController

def get_os_controller():
    """Factory function to instantiate the correct Bluetooth controller based on OS."""
    platform = sys.platform

    if platform.startswith('linux'):
        return LinuxController()
    elif platform == 'darwin':
        return MacController()
    elif platform == 'win32':
        return WindowsController()
    else:
        raise OSError(f"Unsupported operating system: {platform}")