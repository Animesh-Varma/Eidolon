import importlib.util
import os
import platform
import shutil
import ssl
import subprocess
import sys
import urllib.request
import zipfile
import socket

if 'TERM' not in os.environ:
    os.environ['TERM'] = 'xterm-256color'

GREEN, YELLOW, RED, RESET, BOLD = "\033[92m", "\033[93m", "\033[91m", "\033[0m", "\033[1m"


def clear_screen() -> None:
    os.system('cls' if os.name == 'nt' else 'clear')


def is_module_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


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
        "is_apple_silicon": sys.platform == "darwin" and platform.machine() == "arm64",
        "linux_distro": None,
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


def evaluate_environment(sys_info: dict) -> dict:
    status = {
        "deps_installed": all(
            is_module_installed(m) for m in ['speech_recognition', 'numpy', 'sounddevice', 'vosk', 'edge_tts']),
        "model_installed": os.path.isdir("model"),
        "bt_utils_installed": True
    }
    if sys_info['os'].startswith('linux'):
        status['bt_utils_installed'] = shutil.which('bluetoothctl') is not None
    return status


def enforce_sudo_on_linux(sys_info: dict) -> None:
    if sys_info['os'].startswith('linux') and os.geteuid() != 0:
        clear_screen()
        print(f"{RED}{BOLD}[Error] Root privileges required for system setup.{RESET}")
        print("Please re-run this setup script using sudo:\n")
        print(f"  {GREEN}sudo {sys.executable} setup.py{RESET}\n")
        sys.exit(1)


def install_system_dependencies(sys_info: dict) -> None:
    print(f"\n{BOLD}[*] Installing System Packages...{RESET}")
    if sys_info['os'].startswith('linux'):
        if sys_info['pkg_manager'] == 'pacman':
            cmd = "pacman -S --noconfirm bluez bluez-utils portaudio ffmpeg"
        elif sys_info['pkg_manager'] == 'apt':
            cmd = "apt-get update && apt-get install -y bluez bluez-tools portaudio19-dev python3-pyaudio ffmpeg"
        elif sys_info['pkg_manager'] == 'dnf':
            cmd = "dnf install -y bluez bluez-tools portaudio-devel python3-pyaudio ffmpeg"
        else:
            return
        print(f" -> Running: {cmd}")
        run_command(cmd)


def install_python_dependencies() -> None:
    print(f"\n{BOLD}[*] Installing Python dependencies...{RESET}")
    run_command(f"{sys.executable} -m pip install -r requirements.txt")


def download_vosk_model(status: dict) -> None:
    if status['model_installed']: return

    model_url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
    zip_path = "vosk_model.zip"
    print(f"\n{BOLD}[*] Downloading Vosk STT Model (50MB)...{RESET}")

    try:
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(model_url, context=context) as response, open(zip_path, 'wb') as out_file:
            out_file.write(response.read())

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(".")

        os.rename("vosk-model-small-en-us-0.15", "model")
        os.remove(zip_path)

        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and os.path.exists("model"):
            import pwd
            uid = pwd.getpwnam(sudo_user).pw_uid
            gid = pwd.getpwnam(sudo_user).pw_gid
            for root, dirs, files in os.walk("model"):
                for d in dirs: os.chown(os.path.join(root, d), uid, gid)
                for f in files: os.chown(os.path.join(root, f), uid, gid)
            os.chown("model", uid, gid)

        print(f" {GREEN}-> Vosk model successfully installed.{RESET}")
    except Exception as e:
        print(f"{RED}[Error] Failed to download model: {e}{RESET}")


def get_current_ssid() -> str:
    """Attempt to auto-detect the currently connected Wi-Fi SSID."""
    try:
        if sys.platform == 'darwin':
            out = subprocess.check_output(['networksetup', '-getairportnetwork', 'en0'], stderr=subprocess.DEVNULL,
                                          text=True)
            if "Current Wi-Fi Network" in out:
                return out.split(": ")[1].strip()
        elif sys.platform.startswith('linux'):
            if shutil.which('iwgetid'):
                out = subprocess.check_output(['iwgetid', '-r'], stderr=subprocess.DEVNULL, text=True)
                return out.strip()
        elif sys.platform == 'win32':
            out = subprocess.check_output(['netsh', 'wlan', 'show', 'interfaces'], stderr=subprocess.DEVNULL, text=True)
            for line in out.split('\n'):
                if " SSID" in line and "BSSID" not in line:
                    return line.split(":")[1].strip()
    except Exception:
        pass
    return ""


def configure_firmware_credentials():
    print(f"\n{BOLD}========================================{RESET}")
    print(f"{BOLD}      ESP32 NETWORK CONFIGURATION       {RESET}")
    print(f"{BOLD}========================================{RESET}")

    # Attempt to auto-detect the Host IP address on the local network
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        default_ip = s.getsockname()[0]
        s.close()
    except Exception:
        default_ip = "192.168.1.100"

    default_ssid = get_current_ssid()

    print(f"{YELLOW}The ESP32 needs your Wi-Fi credentials to bridge audio to this machine.{RESET}\n")

    while True:
        if default_ssid:
            ssid = input(f"Enter Wi-Fi SSID [{default_ssid}]: ").strip()
            if not ssid:
                ssid = default_ssid
        else:
            ssid = input(f"Enter Wi-Fi SSID: ").strip()

        if not ssid:
            continue

        # --- 5GHz Compatibility Warning ---
        if "5g" in ssid.lower() or "5.8" in ssid:
            print(f"\n{YELLOW}{BOLD}[!] WARNING: Possible 5GHz Wi-Fi Network Detected!{RESET}")
            print(f"{YELLOW}Standard ESP32 chips (like ESP32-WROOM) DO NOT support 5GHz Wi-Fi.{RESET}")
            print(f"{YELLOW}If this is a 5GHz-only network, the ESP32 will fail to connect.{RESET}")
            confirm = input(f"Are you sure your specific ESP32 board supports 5GHz? (y/N): ").strip().lower()
            if confirm != 'y':
                print(f"{GREEN}Let's try again with a 2.4GHz network...{RESET}\n")
                default_ssid = ""  # Clear auto-fill to force re-type
                continue

        break  # SSID is approved

    password = input(f"Enter Wi-Fi Password: ").strip()

    host_ip = input(f"Enter Host Machine IP [{default_ip}]: ").strip()
    if not host_ip:
        host_ip = default_ip

    # Generate the header file for the C compiler
    header_path = os.path.join("esp32_firmware", "eidolon_hfp", "main", "wifi_credentials.h")

    os.makedirs(os.path.dirname(header_path), exist_ok=True)

    with open(header_path, "w") as f:
        f.write("#pragma once\n\n")
        f.write(f'#define WIFI_SSID      "{ssid}"\n')
        f.write(f'#define WIFI_PASS      "{password}"\n')
        f.write(f'#define HOST_IP        "{host_ip}"\n')

    print(f"\n{GREEN}[*] Credentials safely generated in: {header_path}{RESET}")


def setup_esp32_flasher():
    print(f"\n{BOLD}========================================{RESET}")
    print(f"{BOLD}      ESP32 FIRMWARE FLASH UTILITY      {RESET}")
    print(f"{BOLD}========================================{RESET}")

    base_dir = os.getcwd()
    firmware_dir = os.path.join(base_dir, "esp32_firmware", "eidolon_hfp")

    script_name = "idc_run.sh" if os.path.exists(
        os.path.join(base_dir, "esp32_firmware", "idc_run.sh")) else "idf_run.sh"
    script_path = os.path.join("..", script_name)

    if not os.path.exists(firmware_dir):
        print(f"{YELLOW}[!] ESP32 Firmware directory not found at {firmware_dir}. Skipping...{RESET}")
        return

    choice = input("Do you want to flash the ESP32 Bridge over USB right now? (y/N): ").strip().lower()

    if choice == 'y':
        port = input(f"Enter the serial port (e.g. /dev/cu.usbserial-110) or press Enter to auto-detect: ").strip()

        os.chdir(firmware_dir)

        cmd_args = ["bash", script_path]
        if port:
            cmd_args.extend(["-p", port])

        cmd_args.extend(["build", "flash"])

        if os.geteuid() == 0 and sys.platform.startswith('linux'):
            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user:
                print(f"{YELLOW}[!] Dropping to user '{sudo_user}' to preserve ESP-IDF Environment...{RESET}")
                cmd_args = ["sudo", "-u", sudo_user] + cmd_args

        print(f"\n{YELLOW}[*] Executing in {os.getcwd()}: {' '.join(cmd_args)}{RESET}")
        try:
            subprocess.run(cmd_args, check=True)
        except Exception as e:
            print(f"{RED}[Error] Failed to execute ESP-IDF script: {e}{RESET}")
            os.chdir(base_dir)
            return
        finally:
            os.chdir(base_dir)


def main() -> None:
    sys_info = get_system_info()
    status = evaluate_environment(sys_info)

    enforce_sudo_on_linux(sys_info)

    clear_screen()
    print(f"{BOLD}========================================{RESET}")
    print(f"{BOLD}      EIDOLON ENVIRONMENT SETUP         {RESET}")
    print(f"{BOLD}========================================{RESET}\n")

    install_system_dependencies(sys_info)
    install_python_dependencies()
    download_vosk_model(status)

    # Prompt for credentials dynamically before flashing
    configure_firmware_credentials()

    print(f"\n{BOLD}========================================{RESET}")
    print(f"{GREEN}{BOLD} ENVIRONMENT SETUP COMPLETE!{RESET}")
    print(f"{BOLD}========================================{RESET}")

    setup_esp32_flasher()

    # --- FINAL INSTRUCTIONS ---
    print(f"\n{BOLD}{GREEN}===================================================={RESET}")
    print(f"{BOLD}{GREEN}  SUCCESS: Environment & Firmware Ready!{RESET}")
    print(f"{BOLD}{GREEN}===================================================={RESET}")
    print(f"{YELLOW}To start the AI Bridge, please open a NEW terminal window and run:{RESET}")

    if sys_info['os'].startswith('linux'):
        print(f"  {BOLD}sudo {sys.executable} main.py{RESET}\n")
    else:
        print(f"  {BOLD}{sys.executable} main.py{RESET}\n")


if __name__ == "__main__":
    main()