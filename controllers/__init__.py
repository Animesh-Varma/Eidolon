import sys
from .base_controller import BaseController


def get_os_controller() -> BaseController:
    """Factory function to instantiate the correct Bluetooth controller based on OS."""
    platform = sys.platform

    if platform.startswith('linux'):
        from .linux_controller import LinuxController
        return LinuxController()
    elif platform == 'darwin':
        from .mac_controller import MacController
        return MacController()
    elif platform == 'win32':
        from .win_controller import WindowsController
        return WindowsController()
    else:
        raise OSError(f"Unsupported operating system: {platform}")