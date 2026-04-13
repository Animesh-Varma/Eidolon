import os
import sys
import ssl
import platform
import subprocess
import urllib.request
import zipfile
import shutil
import importlib.util
import shlex

# Safety catch for IDE terminals (PyCharm/VSCode)
if 'TERM' not in os.environ:
    os.environ['TERM'] = 'xterm-256color'

# --- ANSI Colors ---
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def is_module_installed(module_name):
    """Checks if a python package is importable."""
    return importlib.util.find_spec(module_name) is not None


def run_command(command, env=None, capture_output=False, silent=False):
    """Utility to run shell commands safely."""
    try:
        if isinstance(command, str):
            command = shlex.split(command)
        result = subprocess.run(
            command,
            shell=False,
            check=True,
            env=env,
            text=True,
            capture_output=capture_output,
            stdout=subprocess.DEVNULL if silent and not capture_output else None,
            stderr=subprocess.DEVNULL if silent and not capture_output else None
        )
        return result.stdout.strip() if capture_output else True
    except subprocess.CalledProcessError:
        return False


def check_homebrew():
    if shutil.which('brew'): return True
    if os.path.exists('/opt/homebrew/bin/brew'):
        os.environ['PATH'] += os.pathsep + '/opt/homebrew/bin'
        return True
    if os.path.exists('/usr/local/bin/brew'):
        os.environ['PATH'] += os.pathsep + '/usr/local/bin'
        return True
    return False


def evaluate_environment(sys_info):
    """Runs checks to see what is already installed."""
    status = {
        "brew_installed": False,
        "portaudio_installed": False,
        "pyaudio_installed": is_module_installed('pyaudio'),
        "deps_installed": all([
            is_module_installed('speech_recognition'),
            is_module_installed('numpy'),
            is_module_installed('sounddevice'),
            is_module_installed('vosk'),
            is_module_installed('pyttsx3')
        ]),
        "model_installed": os.path.isdir("model")
    }

    if sys_info['os'] == 'darwin':
        status['deps_installed'] = status['deps_installed'] and is_module_installed('objc')
        status['brew_installed'] = check_homebrew()
        if status['brew_installed']:
            status['portaudio_installed'] = run_command("brew ls --versions portaudio", capture_output=True) != ""
    elif sys_info['os'].startswith('linux'):
        status['portaudio_installed'] = run_command("dpkg -s portaudio19-dev", capture_output=True,
                                                    silent=True) != False
    else:
        status['portaudio_installed'] = True

    return status


def get_system_info():
    return {
        "os": sys.platform,
        "arch": platform.machine(),
        "is_apple_silicon": sys.platform == "darwin" and platform.machine() == "arm64"
    }


def format_step(is_done, text):
    if is_done:
        return f" {GREEN}[✔]{RESET} {text}"
    else:
        return f" [ ] {text}"


def print_plan(sys_info, status):
    clear_screen()
    print(f"{BOLD}========================================{RESET}")
    print(f"{BOLD}      EIDOLON ENVIRONMENT SETUP{RESET}")
    print(f"{BOLD}========================================{RESET}\n")
    print(f"Detected OS:   {sys_info['os']}")
    print(f"Architecture:  {sys_info['arch']}\n")

    print("The following actions will be performed:")

    # 1. System Dependencies
    if sys_info['os'] == 'darwin':
        if not status['brew_installed']:
            print(f" {RED}[x]{RESET} Install Homebrew (Missing!)")
        print(format_step(status['portaudio_installed'],
                          "Install 'portaudio' via Homebrew (required for Mac Audio I/O)."))
        if sys_info['is_apple_silicon']:
            print(format_step(status['pyaudio_installed'],
                              "Install PyAudio using Apple Silicon specific compiler flags."))
        else:
            print(format_step(status['pyaudio_installed'], "Install PyAudio."))
    elif sys_info['os'].startswith('linux'):
        print(
            format_step(status['portaudio_installed'], "Install 'portaudio19-dev' via apt (required for Linux Audio)."))
        print(format_step(status['pyaudio_installed'], "Install PyAudio."))
    elif sys_info['os'] == 'win32':
        print(format_step(status['pyaudio_installed'], "Install PyAudio."))

    # 2. Python Dependencies
    print(format_step(status['deps_installed'], "Install core Python dependencies from requirements.txt."))

    # 3. Model
    print(format_step(status['model_installed'], "Download and extract the Vosk Offline STT Model (50MB)."))
    print(f"\n{BOLD}========================================{RESET}")


def install_system_dependencies(sys_info, status):
    print(f"\n{BOLD}[*] Checking system dependencies...{RESET}")
    if sys_info['os'] == 'darwin':
        if not status['brew_installed']:
            print(f"{YELLOW}[!] Homebrew is not installed. It is required to install macOS audio libraries.{RESET}")
            ans = input("Would you like to install Homebrew now? (Requires admin password) (y/n): ").strip().lower()
            if ans in ['y', 'yes']:
                print(" -> Running Homebrew installer...")
                subprocess.run(
                    ['/bin/bash', '-c',
                     '$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)'],
                    check=True)
                check_homebrew()
            else:
                print(f"{RED}[Error] Cannot proceed without Homebrew. Exiting.{RESET}")
                sys.exit(1)

        if not status['portaudio_installed']:
            print(" -> Running 'brew install portaudio'...")
            run_command("brew install portaudio")
        else:
            print(" -> PortAudio is already installed.")

    elif sys_info['os'].startswith('linux'):
        if not status['portaudio_installed']:
            print(" -> Running 'sudo apt-get install portaudio19-dev python3-pyaudio'...")
            run_command("sudo apt-get update")
            run_command("sudo apt-get install -y portaudio19-dev python3-pyaudio")
        else:
            print(" -> PortAudio is already installed.")


def install_python_dependencies(sys_info, status):
    print(f"\n{BOLD}[*] Installing Python dependencies...{RESET}")

    if not status['pyaudio_installed']:
        if sys_info['is_apple_silicon']:
            print(" -> Apple Silicon detected. Applying custom PyAudio build flags...")
            try:
                brew_prefix = subprocess.run(['brew', '--prefix'], capture_output=True, text=True,
                                             check=True).stdout.strip()
                custom_env = os.environ.copy()
                custom_env['CFLAGS'] = f"-I{brew_prefix}/include"
                custom_env['LDFLAGS'] = f"-L{brew_prefix}/lib"
                run_command(f"{sys.executable} -m pip install pyaudio", env=custom_env)
            except Exception as e:
                print(f"{YELLOW}[Warning] Custom PyAudio install failed: {e}. Attempting standard install...{RESET}")
                run_command(f"{sys.executable} -m pip install pyaudio")
        else:
            run_command(f"{sys.executable} -m pip install pyaudio")
    else:
        print(" -> PyAudio is already installed.")

    if not status['deps_installed']:
        print(" -> Installing requirements.txt...")
        run_command(f"{sys.executable} -m pip install -r requirements.txt")
    else:
        print(" -> Core dependencies are already installed.")


def download_vosk_model(status):
    if status['model_installed']:
        print(f"\n{BOLD}[*] Vosk model already exists. Skipping download.{RESET}")
        return

    model_dir = "model"
    model_url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
    zip_path = "vosk_model.zip"

    print(f"\n{BOLD}[*] Downloading Vosk STT Model (50MB) from {model_url}...{RESET}")

    try:
        context = ssl.create_default_context()

        with urllib.request.urlopen(model_url, context=context) as response, open(zip_path, 'wb') as out_file:
            data = response.read()
            out_file.write(data)

        print(" -> Download complete. Extracting...")

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            target_dir = os.path.realpath(".")
            for member in zip_ref.namelist():
                member_path = os.path.realpath(os.path.join(target_dir, member))
                if not member_path.startswith(target_dir + os.sep) and member_path != target_dir:
                    raise ValueError(f"Zip entry '{member}' would extract outside target directory")
            zip_ref.extractall(".")

        extracted_folder = "vosk-model-small-en-us-0.15"
        if os.path.exists(extracted_folder):
            os.rename(extracted_folder, model_dir)

        os.remove(zip_path)
        print(f" {GREEN}-> Vosk model successfully installed.{RESET}")
    except Exception as e:
        print(f"\n{RED}[Error] Failed to download or extract the Vosk model: {e}{RESET}")
        print(f"Manual fallback: Download from {model_url} and unzip to a folder named 'model'.")


def main():
    sys_info = get_system_info()
    status = evaluate_environment(sys_info)

    print_plan(sys_info, status)

    if all(status.values()):
        print(f"\n{GREEN}Everything is already set up! You are ready to go.{RESET}")
        confirm = input("Do you want to run the installer anyway? (y/n): ").strip().lower()
        if confirm not in ['y', 'yes']:
            sys.exit(0)
    else:
        confirm = input("Do you want to proceed with the setup? (y/n): ").strip().lower()
        if confirm not in ['y', 'yes']:
            print("Setup aborted by user.")
            sys.exit(0)

    install_system_dependencies(sys_info, status)
    install_python_dependencies(sys_info, status)
    download_vosk_model(status)

    print(f"\n{BOLD}========================================{RESET}")
    print(f"{GREEN}{BOLD} SETUP COMPLETE!{RESET}")
    print(f"{BOLD}========================================{RESET}")

    if sys_info['os'] == 'darwin':
        print(f"\n{YELLOW}[IMPORTANT] macOS Privacy Permissions:{RESET}")
        print(" 1. Go to System Settings > Privacy & Security > Bluetooth")
        print("    -> Grant access to your Terminal / IDE.")
        print(" 2. Go to System Settings > Privacy & Security > Microphone")
        print("    -> Grant access to your Terminal / IDE.")
        print(f"    {YELLOW}(If you skip the microphone step, PyAudio will record pure silence or crash).{RESET}")

    print(f"\n{BOLD}You can now start the proxy by running:{RESET} {GREEN}python main.py{RESET}")


if __name__ == "__main__":
    main()
