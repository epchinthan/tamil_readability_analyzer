"""
setup.py — Complete installer for Tamil Book Readability Analyzer.

What it does:
  1. checks Python version
  2. creates a local virtual environment in .venv
  3. installs all Python packages into .venv
  4. best-effort installs/checks OCR system tools:
       - Tesseract OCR
       - Tamil traineddata (tam)
       - Poppler tools for PDF rendering
  5. creates required folders and launcher scripts

Run:
  python setup.py
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_PYTHON = (3, 8)
ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQ = ROOT / "requirements.txt"


def run(cmd, check=False, capture=True, shell=False):
    print("    $", cmd if isinstance(cmd, str) else " ".join(map(str, cmd)))
    return subprocess.run(
        cmd,
        check=check,
        shell=shell,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def is_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def venv_python() -> Path:
    if platform.system() == "Windows":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def venv_pip() -> Path:
    if platform.system() == "Windows":
        return VENV / "Scripts" / "pip.exe"
    return VENV / "bin" / "pip"


def ensure_python_version():
    if sys.version_info < REQUIRED_PYTHON:
        print(f"✗ Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+ required.")
        print(f"  You have: {sys.version.split()[0]}")
        sys.exit(1)
    print(f"✓ Python {sys.version.split()[0]}")


def install_python_venv_package_best_effort():
    """Install the OS package needed for `python -m venv` on Debian/Ubuntu."""
    if not apt_available():
        return False

    major, minor = sys.version_info[:2]
    exact_pkg = f"python{major}.{minor}-venv"
    commands = [
        f"sudo apt-get update && sudo apt-get install -y {exact_pkg}",
        "sudo apt-get update && sudo apt-get install -y python3-venv",
    ]

    print("\nThe Python virtual-environment module is missing or incomplete.")
    print("I will try to install the required Ubuntu/Debian package now.")
    for cmd in commands:
        result = run(cmd, shell=True, capture=False)
        if result.returncode == 0:
            print("✓ Installed Python venv support")
            return True

    print("⚠ Automatic install failed. Please run this manually, then run ./start.sh again:")
    print(f"  sudo apt-get install {exact_pkg}")
    print("  # or: sudo apt-get install python3-venv")
    return False


def create_venv():
    if venv_python().exists():
        print("✓ Virtual environment already exists: .venv")
        return

    print("\nCreating local virtual environment: .venv")
    result = run([sys.executable, "-m", "venv", str(VENV)], check=False, capture=False)
    if result.returncode == 0:
        return

    if install_python_venv_package_best_effort():
        result = run([sys.executable, "-m", "venv", str(VENV)], check=False, capture=False)
        if result.returncode == 0:
            return

    print("✗ Could not create the local virtual environment.")
    print("  On Ubuntu/Debian, install venv support and try again:")
    print(f"  sudo apt-get install python{sys.version_info.major}.{sys.version_info.minor}-venv")
    sys.exit(1)

def install_python_packages():
    print("\nInstalling Python packages into .venv")
    py = str(venv_python())
    run([py, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], check=True, capture=False)
    run([py, "-m", "pip", "install", "-r", str(REQ)], check=True, capture=False)
    print("✓ Python packages installed")


def apt_available() -> bool:
    return platform.system() == "Linux" and is_cmd("apt-get")


def brew_available() -> bool:
    return platform.system() == "Darwin" and is_cmd("brew")


def winget_available() -> bool:
    return platform.system() == "Windows" and is_cmd("winget")


def no_prompt_mode() -> bool:
    return "--no-prompt" in sys.argv or os.environ.get("TAMIL_ANALYZER_NO_PROMPT") == "1"


def ask_yes_no(prompt: str, default_yes=True) -> bool:
    if no_prompt_mode():
        print(f"{prompt} [{'Y/n' if default_yes else 'y/N'}]: {'yes' if default_yes else 'no'} (auto)")
        return default_yes
    suffix = "[Y/n]" if default_yes else "[y/N]"
    ans = input(f"{prompt} {suffix}: ").strip().lower()
    if not ans:
        return default_yes
    return ans in ("y", "yes")


def install_system_deps_best_effort():
    print("\nChecking OCR system tools")
    missing = []

    if not is_cmd("tesseract"):
        missing.append("tesseract")
    if not is_cmd("pdftoppm") and not is_cmd("pdfinfo"):
        missing.append("poppler")

    if missing:
        print("⚠ Missing system tools:", ", ".join(missing))
    else:
        print("✓ Tesseract and Poppler commands found")

    system = platform.system()

    if apt_available() and missing:
        if ask_yes_no("Install missing OCR tools with apt? This may ask for your password"):
            cmd = "sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-tam poppler-utils"
            result = run(cmd, shell=True, capture=False)
            if result.returncode != 0:
                print("⚠ apt install did not complete. You can run manually:")
                print("  sudo apt-get install tesseract-ocr tesseract-ocr-tam poppler-utils")
    elif brew_available() and missing:
        if ask_yes_no("Install missing OCR tools with Homebrew?"):
            result = run(["brew", "install", "tesseract", "tesseract-lang", "poppler"], capture=False)
            if result.returncode != 0:
                print("⚠ Homebrew install did not complete. You can run manually:")
                print("  brew install tesseract tesseract-lang poppler")
    elif winget_available() and missing:
        print("\nWindows detected. I can try winget installs.")
        print("Tesseract/Poppler installers may open prompts and may require restarting this terminal.")
        if ask_yes_no("Try installing OCR tools with winget?", default_yes=False):
            run(["winget", "install", "--id", "UB-Mannheim.TesseractOCR", "-e"], capture=False)
            run(["winget", "install", "--id", "oschwartz10612.Poppler", "-e"], capture=False)
            print("If commands were installed, close and reopen the terminal, then run python setup.py again.")
    elif missing:
        print("Manual install needed for scanned PDF OCR:")
        if system == "Linux":
            print("  sudo apt-get install tesseract-ocr tesseract-ocr-tam poppler-utils")
        elif system == "Darwin":
            print("  brew install tesseract tesseract-lang poppler")
        else:
            print("  Install Tesseract OCR with Tamil language data and Poppler, then add both to PATH.")

    check_tesseract_tamil()


def check_tesseract_tamil():
    if not is_cmd("tesseract"):
        print("⚠ Tesseract not found. Normal text PDFs still work; scanned PDFs will not OCR.")
        return False
    try:
        out = subprocess.check_output(["tesseract", "--list-langs"], text=True, stderr=subprocess.STDOUT)
        langs = {line.strip() for line in out.splitlines() if line.strip() and "List of" not in line}
        if "tam" in langs:
            print("✓ Tamil OCR language pack available: tam")
            return True
        print("⚠ Tesseract found, but Tamil language pack 'tam' is missing.")
        print("  Linux: sudo apt-get install tesseract-ocr-tam")
        print("  macOS: brew install tesseract-lang")
        print("  Windows: install Tamil traineddata/tam.traineddata into the Tesseract tessdata folder.")
        return False
    except Exception as exc:
        print(f"⚠ Could not check Tesseract languages: {exc}")
        return False


def create_folders():
    for folder in ["uploads", "reports", "logs", "tools"]:
        (ROOT / folder).mkdir(exist_ok=True)
    print("✓ Required folders created")


def write_launchers():
    start_sh = ROOT / "start.sh"
    if start_sh.exists():
        start_sh.chmod(0o755)
        print("✓ Launcher present: start.sh")
    else:
        start_sh.write_text('#!/usr/bin/env bash\nset -e\ncd "$(dirname "$0")"\npython3 setup.py --no-prompt\nexec .venv/bin/python app.py\n', encoding="utf-8")
        start_sh.chmod(0o755)
        print("✓ Launcher created: start.sh")

    start_bat = ROOT / "start.bat"
    if not start_bat.exists():
        start_bat.write_text(r"""@echo off
cd /d "%~dp0"
echo.
echo   Tamil Book Readability Analyzer
echo.
set NEED_SETUP=0
if not exist ".venv\Scripts\python.exe" set NEED_SETUP=1
if not exist ".install_complete" set NEED_SETUP=1

if "%NEED_SETUP%"=="1" (
  echo First run or setup update detected. Preparing everything now...
  python setup.py --no-prompt
  if errorlevel 1 pause & exit /b 1
  type nul > .install_complete
)

echo.
echo Starting server...
echo Open: http://localhost:5000
echo Press Ctrl+C to stop
echo.
".venv\Scripts\python.exe" app.py
pause
""", encoding="utf-8")
        print("✓ Launcher created: start.bat")
    else:
        print("✓ Launcher present: start.bat")


def install_reading_asr(model: str = "small"):
    """Best-effort installer for offline Tamil Reading Practice ASR."""
    model = (model or "small").strip().lower()
    allowed = {"base", "small", "medium"}
    if model not in allowed:
        print(f"⚠ Unknown model '{model}'. Use one of: {', '.join(sorted(allowed))}.")
        model = "small"

    print("\nInstalling optional Reading Practice ASR")
    print(f"Model: {model} (multilingual, suitable for Tamil)")

    if apt_available():
        print("\nInstalling Ubuntu build/audio tools")
        cmd = "sudo apt-get update && sudo apt-get install -y git cmake build-essential ffmpeg"
        result = run(cmd, shell=True, capture=False)
        if result.returncode != 0:
            print("⚠ Could not install system packages automatically.")
            print("  Please run: sudo apt-get install git cmake build-essential ffmpeg")
            return False
    else:
        missing = [c for c in ["git", "cmake", "ffmpeg"] if not is_cmd(c)]
        if missing:
            print("⚠ Missing commands:", ", ".join(missing))
            print("  Install git, cmake, build tools, and ffmpeg first.")
            return False

    tools = ROOT / "tools"
    tools.mkdir(exist_ok=True)
    repo = tools / "whisper.cpp"
    if not repo.exists():
        print("\nDownloading whisper.cpp")
        result = run(["git", "clone", "https://github.com/ggml-org/whisper.cpp.git", str(repo)], capture=False)
        if result.returncode != 0:
            print("✗ Could not clone whisper.cpp. Check internet access.")
            return False
    else:
        print("✓ whisper.cpp already exists")

    print("\nBuilding whisper.cpp")
    print("    $ cmake -B build")
    result = subprocess.run(["cmake", "-B", "build"], cwd=repo, text=True)
    if result.returncode == 0:
        print("    $ cmake --build build -j --config Release")
        result = subprocess.run(["cmake", "--build", "build", "-j", "--config", "Release"], cwd=repo, text=True)
    if result.returncode != 0:
        print("✗ whisper.cpp build failed.")
        return False

    model_path = repo / "models" / f"ggml-{model}.bin"
    if not model_path.exists():
        print("\nDownloading multilingual Whisper model")
        result = subprocess.run(["sh", "./models/download-ggml-model.sh", model], cwd=repo, text=True)
        if result.returncode != 0:
            print("✗ Model download failed.")
            return False
    else:
        print(f"✓ Model already exists: {model_path.name}")

    whisper_bin = repo / "build" / "bin" / "whisper-cli"
    print("\nReading Practice ASR installed.")
    print("start.sh will auto-detect it. Manual environment values:")
    print(f"  export WHISPER_CPP_BIN={whisper_bin}")
    print(f"  export WHISPER_CPP_MODEL={model_path}")
    return True

def run_health_check():
    print("\nRunning health check")
    py = str(venv_python())
    result = run([py, str(ROOT / "doctor.py")], capture=True)
    print(result.stdout or "")
    return result.returncode == 0


def main():
    if "--install-reading-asr" in sys.argv:
        idx = sys.argv.index("--install-reading-asr")
        model = sys.argv[idx + 1] if len(sys.argv) > idx + 1 and not sys.argv[idx + 1].startswith("--") else "small"
        ensure_python_version()
        create_folders()
        ok = install_reading_asr(model)
        sys.exit(0 if ok else 1)

    print("\n" + "=" * 58)
    print(" Tamil Book Readability Analyzer — Complete Installer")
    print("=" * 58)
    ensure_python_version()
    create_venv()
    install_python_packages()
    install_system_deps_best_effort()
    create_folders()
    write_launchers()
    run_health_check()

    print("\n" + "=" * 58)
    print(" Setup finished")
    print("=" * 58)
    print("Start the app with:")
    if platform.system() == "Windows":
        print("  start.bat")
    else:
        print("  ./start.sh")
    print("Then open: http://localhost:5000")

    if not no_prompt_mode() and ask_yes_no("Start the app now?", default_yes=False):
        os.chdir(ROOT)
        os.execv(str(venv_python()), [str(venv_python()), "app.py"])


if __name__ == "__main__":
    main()
