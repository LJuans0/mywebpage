#!/usr/bin/env python3
"""Genera el instalador del sistema donde se ejecuta (PyInstaller no compila de un SO a otro).
Requisitos del que compila: pip install pyinstaller pystray pillow
Resultado: dist/DiscBoxSetup.exe (Windows) o dist/DiscBoxSetup (Linux)."""
import os, subprocess, sys
from pathlib import Path

here = Path(__file__).resolve().parent
os.chdir(here)
WIN = os.name == "nt"
app_name = "DiscBox" if WIN else "discbox"


def pyi(*args):
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", *args], check=True)


# 1) La app (un solo ejecutable, sin consola)
pyi("--onefile", "--windowed", "--name", app_name, "--collect-submodules", "pystray", "discbox.py")
app_exe = here / "dist" / (app_name + (".exe" if WIN else ""))

# 2) El instalador, con la app dentro
pyi("--onefile", "--windowed", "--name", "DiscBoxSetup", "--add-data", f"{app_exe}{os.pathsep}.", "instalar.py")
print("\nListo:", here / "dist" / ("DiscBoxSetup.exe" if WIN else "DiscBoxSetup"))
