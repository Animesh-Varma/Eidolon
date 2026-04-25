import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import socket

if 'TERM' not in os.environ:
    os.environ['TERM'] = 'xterm-256color'

GREEN, YELLOW, RED, RESET, BOLD = "\033[92m", "\033[93m", "\033[91m", "\033[0m", "\033[1m"


def clear_screen() -> None:
    os.system('cls' if os.name == 'nt' else 'clear')


def run_command(command: str, env: dict = None, capture_output: bool = False, silent: bool = False):
    try:
        result = subprocess.run(
            command, shell=True, check=True, env=env, text=True,
            capture_output=capture_output,
            stdout=subprocess.DEVNULL if silent and not capture_output else None,
            stderr=subprocess.DEVNULL if silent and not capture_output else None
        )
        return result.stdout.strip() if capture_output else True
    except subprocess.CalledProcessError:
        return False


def get_system_info() -> dict:
    sys_info = {
        "os": sys.platform,
        "arch": platform.machine(),
        "pkg_manager": None
    }
    if sys_info['os'].startswith('linux'):
        if shutil.which('pacman'):
            sys_info['pkg_manager'] = "pacman"
        elif shutil.which('apt-get'):
            sys_info['pkg_manager'] = "apt"
        elif shutil.which('dnf'):
            sys_info['pkg_manager'] = "dnf"
    return sys_info


def enforce_sudo_on_linux(sys_info: dict) -> None:
    if sys_info['os'].startswith('linux') and os.geteuid() != 0:
        clear_screen()
        print(f"{RED}{BOLD}[Error] Root privileges required for system setup.{RESET}")
        print("Please re-run this setup script using sudo:\n")
        print(f"  {GREEN}sudo {sys.executable} setup.py{RESET}\n")
        sys.exit(1)


def prompt_architecture() -> str:
    print(f"\n{BOLD}========================================{RESET}")
    print(f"{BOLD}      EIDOLON ARCHITECTURE SETUP        {RESET}")
    print(f"{BOLD}========================================{RESET}")
    print("Select your intended Bluetooth Architecture:")
    print("  [1] Native OS Bluetooth (Direct via computer hardware)")
    print("  [2] ESP32 Wi-Fi Audio Bridge (Dedicated hardware delegate)")

    choice = ""
    while choice not in ['1', '2']:
        choice = input(f"\nEnter your choice [1/2]: ").strip()
    return choice


def install_system_dependencies(sys_info: dict, arch_choice: str) -> None:
    print(f"\n{BOLD}[*] Installing System Packages...{RESET}")
    sudo_user = os.environ.get("SUDO_USER")

    if sys_info['os'].startswith('linux'):
        # 1. Handle Package Installation
        pacman_pkgs = "bluez bluez-utils portaudio ffmpeg"
        apt_pkgs = "bluez bluez-tools portaudio19-dev python3-pyaudio ffmpeg"
        dnf_pkgs = "bluez bluez-tools portaudio-devel python3-pyaudio ffmpeg"

        if arch_choice == '2':
            pacman_pkgs += " git wget make ncurses flex bison gperf python cmake ninja ccache dfu-util libusb"
            apt_pkgs += " git wget flex bison gperf python3 python3-pip python3-venv cmake ninja-build ccache libffi-dev libssl-dev dfu-util libusb-1.0-0"
            dnf_pkgs += " git wget make ncurses-devel flex bison gperf python3 python3-pip cmake ninja-build ccache dfu-util libusbx"

        if sys_info['pkg_manager'] == 'pacman':
            cmd = f"pacman -S --needed --noconfirm {pacman_pkgs}"
            serial_group = "uucp"
        elif sys_info['pkg_manager'] == 'apt':
            cmd = f"apt-get update && apt-get install -y {apt_pkgs}"
            serial_group = "dialout"
        elif sys_info['pkg_manager'] == 'dnf':
            cmd = f"dnf install -y {dnf_pkgs}"
            serial_group = "dialout"
        else:
            return

        run_command(cmd)

        # 2. Handle Serial Port Permissions (uucp/dialout)
        if arch_choice == '2' and sudo_user:
            print(f"{GREEN}[*] Adding user '{sudo_user}' to '{serial_group}' group for ESP32 flashing...{RESET}")
            run_command(f"usermod -aG {serial_group} {sudo_user}")


def install_python_dependencies() -> None:
    print(f"\n{BOLD}[*] Installing Python dependencies...{RESET}")
    run_command(f"{sys.executable} -m pip install -r requirements.txt")


def install_esp_idf():
    print(f"\n{BOLD}========================================{RESET}")
    print(f"{BOLD}      ESP-IDF TOOLCHAIN SETUP           {RESET}")
    print(f"{BOLD}========================================{RESET}")

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.geteuid() == 0:
        home_dir = os.path.expanduser(f"~{sudo_user}")
        run_as = f"sudo -H -u {sudo_user} bash -c "
    else:
        home_dir = os.path.expanduser("~")
        run_as = "bash -c "

    esp_dir = os.path.join(home_dir, "esp")
    idf_path = os.path.join(esp_dir, "esp-idf")

    if os.path.exists(os.path.join(idf_path, "export.sh")):
        print(f"{GREEN}[*] ESP-IDF already installed.{RESET}")
        return

    print(f"{YELLOW}[*] Installing ESP-IDF to {idf_path}...{RESET}")
    os.makedirs(esp_dir, exist_ok=True)
    if sudo_user: run_command(f"chown -R {sudo_user}:{sudo_user} {esp_dir}")

    run_command(f"{run_as} 'git clone -b v5.3.1 --recursive https://github.com/espressif/esp-idf.git \"{idf_path}\"'")
    run_command(f"{run_as} 'cd \"{idf_path}\" && ./install.sh esp32'")


def configure_firmware_credentials():
    print(f"\n{BOLD}========================================{RESET}")
    print(f"{BOLD}      ESP32 NETWORK CONFIGURATION       {RESET}")
    print(f"{BOLD}========================================{RESET}")

    header_path = os.path.join("esp32_firmware", "eidolon_hfp", "main", "wifi_credentials.h")

    # Prompt logic
    ssid = input("Enter Wi-Fi SSID: ").strip()
    password = input("Enter Wi-Fi Password: ").strip()

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM);
        s.connect(("8.8.8.8", 80))
        default_ip = s.getsockname()[0];
        s.close()
    except:
        default_ip = "192.168.1.100"

    host_ip = input(f"Enter Host IP [{default_ip}]: ").strip() or default_ip

    os.makedirs(os.path.dirname(header_path), exist_ok=True)
    with open(header_path, "w") as f:
        f.write(
            f'#pragma once\n#define WIFI_SSID "{ssid}"\n#define WIFI_PASS "{password}"\n#define HOST_IP "{host_ip}"\n')

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user: run_command(f"chown -R {sudo_user}:{sudo_user} {os.path.dirname(header_path)}")


def setup_esp32_flasher():
    print(f"\n{BOLD}========================================{RESET}")
    print(f"{BOLD}      ESP32 FIRMWARE FLASH UTILITY      {RESET}")
    print(f"{BOLD}========================================{RESET}")

    choice = input("Do you want to flash the ESP32 now? (y/N): ").strip().lower()
    if choice != 'y': return

    firmware_dir = os.path.join(os.getcwd(), "esp32_firmware", "eidolon_hfp")
    sudo_user = os.environ.get("SUDO_USER")

    # We attempt a manual chmod on the serial port for this session only,
    # since group changes haven't kicked in yet.
    if sys.platform.startswith('linux'):
        print(f"{YELLOW}[*] Attempting to temporarily unlock serial ports for flashing...{RESET}")
        run_command("chmod 666 /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || true")

    shell_cmd = f"cd '{firmware_dir}' && bash '../idf_run.sh' build flash"
    cmd_args = ["sudo", "-i", "-u", sudo_user, "bash", "-c", shell_cmd]

    try:
        subprocess.run(cmd_args, check=True)
    except:
        print(f"{RED}[!] Flash failed. This is likely because serial permissions haven't applied yet.{RESET}")


def main() -> None:
    sys_info = get_system_info()
    enforce_sudo_on_linux(sys_info)
    clear_screen()

    arch_choice = prompt_architecture()

    install_system_dependencies(sys_info, arch_choice)
    install_python_dependencies()

    if arch_choice == '2':
        install_esp_idf()
        configure_firmware_credentials()
        setup_esp32_flasher()

    print(f"\n{BOLD}{GREEN}===================================================={RESET}")
    print(f"{BOLD}{GREEN}  SUCCESS: Environment Ready!{RESET}")
    print(f"{BOLD}{GREEN}===================================================={RESET}")

    if arch_choice == '2':
        print(f"{YELLOW}{BOLD}[CRITICAL] ACTION REQUIRED:{RESET}")
        print(f"{YELLOW}To use the ESP32, you might need to Log Out and Log Back In (or reboot).{RESET}")
        print(f"{YELLOW}This applies the new 'uucp/dialout' serial port permissions.{RESET}\n")

    print(f"To start Eidolon, run: {BOLD}sudo {sys.executable} main.py{RESET}\n")


if __name__ == "__main__":
    main()