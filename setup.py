import importlib.util
import os
import platform
import shutil
import ssl
import subprocess
import sys
import urllib.request
import zipfile

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
    """Gracefully exits and provides the exact command if not running as root on Linux."""
    if sys_info['os'].startswith('linux') and os.geteuid() != 0:
        clear_screen()
        print(f"{RED}{BOLD}[Error] Root privileges required for system setup.{RESET}")
        print("This script needs to install packages via your package manager (pacman/apt/dnf).")
        print("Please re-run this setup script using sudo, pointing to your virtual environment:\n")
        print(f"  {GREEN}sudo {sys.executable} setup.py{RESET}\n")
        sys.exit(1)


def apply_linux_audio_overrides() -> None:
    print(f"\n{BOLD}[*] Configuring Linux Sound Server Overrides...{RESET}")

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd
        home = pwd.getpwnam(sudo_user).pw_dir
    else:
        home = os.path.expanduser("~")

    if shutil.which("wireplumber"):
        wp_dir = os.path.join(home, ".config", "wireplumber", "wireplumber.conf.d")
        os.makedirs(wp_dir, exist_ok=True)
        wp_file = os.path.join(wp_dir, "51-disable-hfp.conf")

        with open(wp_file, "w") as f:
            f.write("monitor.bluez.properties = {\n  bluez5.roles = [ a2dp_sink a2dp_source ]\n}\n")

        if sudo_user:
            uid = pwd.getpwnam(sudo_user).pw_uid
            gid = pwd.getpwnam(sudo_user).pw_gid
            os.chown(wp_file, uid, gid)
            os.chown(wp_dir, uid, gid)

        print(f" {GREEN}[✔]{RESET} WirePlumber HFP grab disabled. (Applied to user {sudo_user or 'current'})")
        print(f" {YELLOW}-> Please run `systemctl --user restart wireplumber` AFTER setup, or reboot.{RESET}")

    elif shutil.which("pulseaudio"):
        print(
            f"{YELLOW}[!] Notice: Running PulseAudio. You may need to manually unload module-bluetooth-discover.{RESET}")


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
            print(f"{YELLOW}[!] Unknown package manager. Please install bluez, portaudio, and ffmpeg manually.{RESET}")
            return

        print(f" -> Running: {cmd}")
        run_command(cmd)
        apply_linux_audio_overrides()


def install_python_dependencies() -> None:
    print(f"\n{BOLD}[*] Installing Python dependencies...{RESET}")
    run_command(f"{sys.executable} -m pip install -r requirements.txt")


def download_vosk_model(status: dict) -> None:
    if status['model_installed']:
        return

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


def main() -> None:
    sys_info = get_system_info()
    status = evaluate_environment(sys_info)

    enforce_sudo_on_linux(sys_info)

    clear_screen()
    print(f"{BOLD}========================================{RESET}")
    print(f"{BOLD}      EIDOLON ENVIRONMENT SETUP{RESET}")
    print(f"{BOLD}========================================{RESET}\n")
    print(f"Detected OS: {sys_info['os']} ({sys_info.get('pkg_manager', 'N/A')})\n")

    install_system_dependencies(sys_info, status)
    install_python_dependencies()
    download_vosk_model(status)

    print(f"\n{BOLD}========================================{RESET}")
    print(f"{GREEN}{BOLD} SETUP COMPLETE!{RESET}")
    print(f"{BOLD}========================================{RESET}")

    if sys_info['os'].startswith('linux'):
        print(f"\n{YELLOW}[IMPORTANT] Linux Raw Socket Permissions:{RESET}")
        print(" Because this script bypasses DBus and talks to the kernel directly,")
        print(f" you MUST run the main script with sudo:\n")
        print(f" {GREEN}sudo {sys.executable} main.py{RESET}\n")


if __name__ == "__main__":
    main()