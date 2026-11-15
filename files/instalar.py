#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instalador de DiscBox. Se empaqueta con PyInstaller (ver build.py) llevando dentro
el ejecutable de DiscBox: el usuario final no necesita Python."""
import os, queue, shutil, subprocess, sys, threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import discbox as d

EXE_NAME = "DiscBox.exe" if d.WIN else "discbox"
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
TARGET = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DiscBox") if d.WIN else DATA_HOME / "discbox"
NO_WINDOW = 0x08000000 if d.WIN else 0


def bundled_exe():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / EXE_NAME
    return Path(__file__).resolve().parent / "dist" / EXE_NAME  # ejecución desde código (pruebas)


def install(games_dir, autostart, say):
    src = bundled_exe()
    if not src.exists():
        raise RuntimeError(f"Instalador incompleto: falta {EXE_NAME}.")
    TARGET.mkdir(parents=True, exist_ok=True)
    exe = TARGET / EXE_NAME

    say("Copiando archivos…")
    try:
        shutil.copyfile(src, exe)
    except PermissionError:
        raise RuntimeError("DiscBox está en ejecución. Ciérralo desde la bandeja "
                           "(clic derecho → Salir) y vuelve a instalar.")
    if not d.WIN:
        os.chmod(exe, 0o755)

    say("Guardando preferencias…")
    Path(games_dir).mkdir(parents=True, exist_ok=True)
    d.save_config({"autostart": autostart, "games_dir": games_dir})
    d.set_autostart(autostart, [str(exe)])

    say("Creando acceso directo…")
    if d.WIN:
        lnk = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "DiscBox.lnk"
        ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
              f"$s.TargetPath='{exe}';$s.WorkingDirectory='{TARGET}';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                       creationflags=NO_WINDOW, env=d.clean_env())
    else:
        apps = DATA_HOME / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        (apps / "discbox.desktop").write_text(d.desktop_entry([str(exe)]), "utf-8")

    say("Iniciando DiscBox…")
    kw = dict(creationflags=0x00000208) if d.WIN else dict(start_new_session=True)  # desacoplado del instalador
    subprocess.Popen([str(exe)], cwd=str(TARGET), env=d.clean_env(), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def main():
    root = tk.Tk()
    root.title("Instalar DiscBox")
    root.configure(bg=d.BG)
    root.resizable(False, False)
    d.setup_style(root)
    F = d.FAM
    q = queue.Queue()

    f = tk.Frame(root, bg=d.BG, padx=24, pady=20)
    f.pack()
    tk.Label(f, text="Bienvenido a DiscBox", font=(F, 15, "bold"), bg=d.BG, fg=d.ACCENT).pack(anchor="w")
    tk.Label(f, text="Copia tus juegos automáticamente al insertar un disco y los inicia con un clic.\n"
                     "Vive en la bandeja del sistema y consume muy pocos recursos.",
             font=(F, 9), bg=d.BG, fg=d.FG, justify="left", wraplength=380).pack(anchor="w", pady=(6, 16))

    tk.Label(f, text="Preferencias", font=(F, 10, "bold"), bg=d.BG, fg=d.FG).pack(anchor="w")
    auto = tk.BooleanVar(value=True)
    tk.Checkbutton(f, text="Iniciar DiscBox con el sistema", variable=auto, bg=d.BG, fg=d.FG,
                   activebackground=d.BG, selectcolor="white", font=(F, 9), bd=0,
                   highlightthickness=0).pack(anchor="w", pady=(6, 10))

    tk.Label(f, text="Carpeta donde se guardarán los juegos", font=(F, 9), bg=d.BG, fg=d.FG).pack(anchor="w")
    row = tk.Frame(f, bg=d.BG)
    row.pack(fill="x", pady=(2, 0))
    games = tk.StringVar(value=d.DEFAULTS["games_dir"])
    tk.Label(row, textvariable=games, font=(F, 8), bg=d.BG, fg=d.DIM, wraplength=290,
             justify="left").pack(side="left", fill="x", expand=True)

    def browse():
        p = filedialog.askdirectory(parent=root, initialdir=games.get())
        if p:
            games.set(p)
    d.button(row, "Cambiar…", browse).pack(side="right", padx=(10, 0))

    status = tk.Label(f, text="", font=(F, 8), bg=d.BG, fg=d.DIM)
    status.pack(anchor="w", pady=(16, 2))
    bar = ttk.Progressbar(f, style="Accent.Horizontal.TProgressbar", mode="indeterminate")
    btn_row = tk.Frame(f, bg=d.BG)
    btn_row.pack(fill="x", pady=(10, 0))

    def poll():
        try:
            while True:
                kind, msg = q.get_nowait()
                if kind == "say":
                    status.config(text=msg, fg=d.DIM)
                elif kind == "ok":
                    bar.stop(); bar.pack_forget()
                    status.config(text="¡Listo! DiscBox ya está activo en la bandeja del sistema.", fg=d.ACCENT)
                    go.config(text="Cerrar", command=root.destroy, state="normal")
                else:
                    bar.stop(); bar.pack_forget()
                    status.config(text="Error en la instalación.", fg=d.ACCENT)
                    go.config(state="normal")
                    messagebox.showerror("DiscBox", msg, parent=root)
        except queue.Empty:
            pass
        root.after(150, poll)

    def start():
        go.config(state="disabled")
        bar.pack(fill="x", before=btn_row)
        bar.start(12)

        def job():
            try:
                install(games.get(), auto.get(), lambda m: q.put(("say", m)))
                q.put(("ok", ""))
            except Exception as e:
                q.put(("err", str(e)))
        threading.Thread(target=job, daemon=True).start()

    go = d.button(btn_row, "Instalar", start, primary=True)
    go.pack(side="right")
    d.button(btn_row, "Salir", root.destroy).pack(side="right", padx=(0, 8))
    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
