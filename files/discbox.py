#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DiscBox: copia juegos desde discos (multi-disco, .zip y .zip dividido) y los lanza.
Windows + Linux. Vive en la bandeja del sistema. Dependencias: pystray, pillow."""
import base64, glob, io, json, os, queue, re, shutil, socket, subprocess, sys, threading, time, zipfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

APP = "DiscBox"
ACCENT, ACCENT_DK = "#db0063", "#b8004f"
BG, FG, DIM, TRACK = "#fafafa", "#222222", "#8c8c8c", "#e8e8e8"
WIN = os.name == "nt"
MANIFEST = "discbox.json"          # archivo en la raíz de cada disco
PARTIAL = ".discbox_installing"    # marca de instalación incompleta
BANNER, ICON = (460, 215), (64, 64)
CHUNK = 1 << 20
LAUNCH_SECONDS = 5
SKIP = {"system volume information", "$recycle.bin", "autorun.inf", MANIFEST}
NOTE = "La barra muestra el progreso del disco actual, no el total."
FAM = "TkDefaultFont"

CONFIG_DIR = (Path(os.environ.get("APPDATA", Path.home())) / APP) if WIN else \
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "discbox"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULTS = {"autostart": True, "games_dir": str(Path.home() / "games")}


class Cancelled(Exception):
    pass


# ───────────────────────── configuración y autoinicio ─────────────────────────
def load_config():
    try:
        return {**DEFAULTS, **json.loads(CONFIG_FILE.read_text("utf-8"))}
    except Exception:
        return dict(DEFAULTS)


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), "utf-8")


def app_command():
    """Comando que inicia la app: el propio ejecutable (empaquetado) o python + script."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = sys.executable
    if WIN and os.path.basename(exe).lower() == "python.exe":
        exe = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return [exe, os.path.abspath(__file__)]


def quote_cmd(cmd):
    return " ".join(f'"{c}"' for c in cmd)


def clean_env():
    """Entorno sin las variables que inyecta PyInstaller (para no romper juegos ni procesos hijos)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def desktop_entry(command):
    return ("[Desktop Entry]\nType=Application\nName=DiscBox\n"
            "Comment=Copia e inicia juegos desde discos\n"
            f"Exec={quote_cmd(command)}\nIcon=media-optical\nTerminal=false\n"
            "Categories=Utility;\nX-GNOME-Autostart-enabled=true\n")


def set_autostart(enabled, command=None):
    command = command or app_command()
    if WIN:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        try:
            if enabled:
                winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, quote_cmd(command))
            else:
                try:
                    winreg.DeleteValue(key, APP)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
    else:
        f = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "discbox.desktop"
        if enabled:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(desktop_entry(command), "utf-8")
        else:
            f.unlink(missing_ok=True)


# ───────────────────────── detección de discos ─────────────────────────
def optical_discs():
    """{punto_de_montaje: token} de unidades ópticas que tienen un disco dentro."""
    found = {}
    if WIN:
        import ctypes
        k = ctypes.windll.kernel32
        old = k.SetErrorMode(0x8001)  # sin diálogos de "no hay disco"
        try:
            mask = k.GetLogicalDrives()
            for i in range(26):
                root = f"{chr(65 + i)}:\\"
                if mask >> i & 1 and k.GetDriveTypeW(root) == 5:  # DRIVE_CDROM
                    serial = ctypes.c_uint32(0)
                    if k.GetVolumeInformationW(root, None, 0, ctypes.byref(serial), None, None, None, 0):
                        found[root] = serial.value
        finally:
            k.SetErrorMode(old)
    else:
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    dev, mnt, fs = line.split()[:3]
                    if dev.startswith(("/dev/sr", "/dev/cdrom", "/dev/scd")) or fs in ("iso9660", "udf"):
                        mnt = mnt.replace("\\040", " ")
                        try:
                            found[mnt] = (dev, os.stat(mnt).st_mtime_ns)
                        except OSError:
                            pass
        except OSError:
            pass
    return found


def watcher(post):
    """Hilo ligero: consulta cada 2 s; avisa solo cuando entra un disco nuevo."""
    prev = {}
    while True:
        try:
            cur = optical_discs()
        except Exception:
            cur = {}
        for mount, token in cur.items():
            if prev.get(mount) != token:
                post("disc", mount)
        prev = cur
        time.sleep(2)


def safe_name(s):
    return re.sub(r'[<>:"/\\|?*]+', "_", str(s)).strip(". ") or "Game"


def read_manifest(mount):
    try:
        with open(os.path.join(mount, MANIFEST), encoding="utf-8") as f:
            m = json.load(f)
        m["title"] = str(m["title"])
        m["exe"] = str(m["exe"])
        m["id"] = str(m.get("id") or m["title"])
        m["disc"] = int(m.get("disc", 1))
        m["total_discs"] = max(m["disc"], int(m.get("total_discs", 1)))
        m["install_dir"] = safe_name(m.get("install_dir") or m["title"])
        return m
    except Exception:
        return None


def eject(mount):
    try:
        if WIN:
            import ctypes
            mci = ctypes.windll.winmm.mciSendStringW
            mci(f"open {mount[:2]} type cdaudio alias dbx", None, 0, None)
            mci("set dbx door open", None, 0, None)
            mci("close dbx", None, 0, None)
        else:
            subprocess.run(["eject", mount], capture_output=True, timeout=20, env=clean_env())
    except Exception:
        pass


# ───────────────────────── copia y descompresión ─────────────────────────
ZIP_RE = re.compile(r"\.(zip|z\d\d|zip\.\d+)$", re.I)


def copy_disc(mount, dest, cancel, report, final):
    files = []
    for root, dirs, names in os.walk(mount):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP]
        for n in names:
            if n.lower() not in SKIP:
                p = os.path.join(root, n)
                files.append((p, os.path.relpath(p, mount), os.path.getsize(p)))
    total = sum(f[2] for f in files) or 1
    w = 0.5 if any(ZIP_RE.search(f[1]) for f in files) else 1.0  # peso de la copia en la barra
    done = 0
    for src, rel, _ in files:
        out = Path(dest) / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as a, open(out, "wb") as b:
            while True:
                if cancel.is_set():
                    raise Cancelled
                buf = a.read(CHUNK)
                if not buf:
                    break
                b.write(buf)
                done += len(buf)
                report(done / total * w, "")
    report(w, "")
    extract_all(str(dest), cancel, report, w, final)


def find_archives(dest, final):
    """Zip sueltos siempre; zip divididos (.z01 / .zip.001) solo en el último disco."""
    groups = []
    for root, _, names in os.walk(dest):
        low = {n.lower() for n in names}
        for n in names:
            l, p = n.lower(), os.path.join(root, n)
            if l.endswith(".zip"):
                spanned = (l[:-4] + ".z01") in low
                if spanned and not final:
                    continue
                groups.append(("span" if spanned else "zip", p))
            elif final and re.search(r"\.zip\.0*1$", l):
                groups.append(("raw", p))
    return groups


def unzip(zpath, folder, cancel, rep):
    base = os.path.realpath(folder)
    with zipfile.ZipFile(zpath) as zf:
        infos = zf.infolist()
        total, done = sum(i.file_size for i in infos) or 1, 0
        for info in infos:
            target = os.path.realpath(os.path.join(base, info.filename))
            if target != base and not target.startswith(base + os.sep):
                continue  # protección contra rutas maliciosas
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as a, open(target, "wb") as b:
                while True:
                    if cancel.is_set():
                        raise Cancelled
                    buf = a.read(CHUNK)
                    if not buf:
                        break
                    b.write(buf)
                    done += len(buf)
                    rep(done / total)
            if not WIN and (info.external_attr >> 16) & 0o111:
                os.chmod(target, os.stat(target).st_mode | 0o111)


def unzip_spanned(path, folder, cancel, rep):
    """.zip + .z01/.z02…: Python no lo soporta, se usa 7-Zip o Info-ZIP si existen."""
    cands = ["7z", "7zz", "7za", "7zr"]
    if WIN:
        cands += [os.path.join(os.environ.get("ProgramFiles", ""), "7-Zip", "7z.exe")]
    for c in cands:
        exe = shutil.which(c) or (c if os.path.isfile(c) else None)
        if exe:
            r = subprocess.run([exe, "x", "-y", f"-o{folder}", path], capture_output=True, env=clean_env())
            if r.returncode:
                raise RuntimeError("7-Zip no pudo descomprimir " + os.path.basename(path))
            return
    z = shutil.which("zip")
    if z:
        joined = path[:-4] + "_joined.zip"
        if subprocess.run([z, "-s", "0", path, "--out", joined], capture_output=True, env=clean_env()).returncode == 0:
            try:
                unzip(joined, folder, cancel, rep)
            finally:
                os.path.exists(joined) and os.remove(joined)
            return
    raise RuntimeError("Para ZIP divididos en .z01 instala 7-Zip (7z) o zip.")


def extract_all(dest, cancel, report, w, final):
    groups = find_archives(dest, final)
    n = len(groups)
    for i, (kind, path) in enumerate(groups):
        if cancel.is_set():
            raise Cancelled
        folder, name = os.path.dirname(path), os.path.basename(path)
        rep = lambda f, i=i, name=name: report(w + (1 - w) * (i + f) / n, f"Descomprimiendo {name}…")
        rep(0)
        if kind == "raw":  # X.zip.001, X.zip.002… → se unen y se descomprimen
            joined = re.sub(r"\.0*1$", "", path)
            parts = sorted(glob.glob(glob.escape(joined) + ".[0-9]*"), key=lambda p: int(p.rsplit(".", 1)[1]))
            with open(joined, "wb") as out:
                for p in parts:
                    with open(p, "rb") as f:
                        shutil.copyfileobj(f, out, CHUNK)
            unzip(joined, folder, cancel, rep)
            for p in parts + [joined]:
                os.remove(p)
        elif kind == "span":
            unzip_spanned(path, folder, cancel, rep)
            for p in glob.glob(glob.escape(path[:-4]) + ".z[0-9][0-9]") + [path]:
                os.remove(p)
        else:
            unzip(path, folder, cancel, rep)
            os.remove(path)
    report(1.0, "")


# ───────────────────────── interfaz ─────────────────────────
def setup_style(root):
    global FAM
    FAM = tkfont.nametofont("TkDefaultFont").actual("family")
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure("Accent.Horizontal.TProgressbar", troughcolor=TRACK, background=ACCENT,
                 bordercolor=TRACK, lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)


def button(parent, text, cmd, primary=False):
    bg, fg, hov = (ACCENT, "white", ACCENT_DK) if primary else (TRACK, FG, "#d8d8d8")
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=hov, activeforeground=fg,
                  relief="flat", bd=0, padx=16, pady=5, cursor="hand2", highlightthickness=0, font=(FAM, 9))
    b.bind("<Enter>", lambda e: b.config(bg=hov))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


def png_image(path, size, title):
    """Carga una imagen (ajustada a `size`) como PhotoImage; si falta, genera un marcador."""
    from PIL import Image, ImageDraw
    try:
        im = Image.open(path).convert("RGBA")
        if im.size != size:
            im = im.resize(size, Image.LANCZOS)
    except Exception:
        im = Image.new("RGBA", size, ACCENT)
        ImageDraw.Draw(im).text((10, 10), title[:40], fill="white")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


def present(win):
    win.update_idletasks()
    x = (win.winfo_screenwidth() - win.winfo_width()) // 2
    y = (win.winfo_screenheight() - win.winfo_height()) // 3
    win.geometry(f"+{x}+{y}")
    win.attributes("-topmost", True)
    win.after(400, lambda: win.winfo_exists() and win.attributes("-topmost", False))
    win.focus_force()


class GamePopup(tk.Toplevel):
    """Banner 460x215 + icono 64x64 + título + ruta. Ventana ajustada, no redimensionable."""

    def __init__(self, app, m, mount, path):
        super().__init__(app.root)
        self.app, self.job = app, None
        self.configure(bg=BG)
        self.title(m["title"])
        self.resizable(False, False)
        self.banner = png_image(os.path.join(mount, m.get("banner", "banner.png")), BANNER, m["title"])
        self.icon = png_image(os.path.join(mount, m.get("icon", "icon.png")), ICON, m["title"][:2])
        tk.Label(self, image=self.banner, bd=0).pack()
        self.body = tk.Frame(self, bg=BG, padx=16, pady=14)
        self.body.pack(fill="x")
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x")
        tk.Label(row, image=self.icon, bd=0).pack(side="left")
        info = tk.Frame(row, bg=BG)
        info.pack(side="left", fill="x", expand=True, padx=(12, 0))
        tk.Label(info, text=m["title"], font=(FAM, 13, "bold"), bg=BG, fg=FG, anchor="w",
                 justify="left", wraplength=340).pack(fill="x")
        tk.Label(info, text=str(path), font=(FAM, 8), bg=BG, fg=DIM, anchor="w",
                 justify="left", wraplength=340).pack(fill="x")

    def close(self):
        if self.job:
            self.after_cancel(self.job)
        if self.winfo_exists():
            self.destroy()


class InstallPopup(GamePopup):
    def __init__(self, app, m, mount, dest):
        super().__init__(app, m, mount, dest)
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=(16, 0))
        self.lbl = tk.Label(row, font=(FAM, 9), bg=BG, fg=FG, width=19, anchor="w")
        self.lbl.pack(side="left")
        self.bar = ttk.Progressbar(row, style="Accent.Horizontal.TProgressbar", maximum=1000)
        self.bar.pack(side="left", fill="x", expand=True)
        self.note = tk.Label(self.body, font=(FAM, 8), bg=BG, fg=DIM, anchor="w", justify="left", wraplength=428)
        self.note.pack(fill="x", pady=(5, 0))
        btns = tk.Frame(self.body, bg=BG)
        btns.pack(fill="x", pady=(12, 0))
        button(btns, "Cancelar", app.cancel_install).pack(side="right")
        self.cont = button(btns, "Continuar", app.rescan, primary=True)
        self.protocol("WM_DELETE_WINDOW", app.cancel_install)
        present(self)

    def show_progress(self, disc, total):
        left = total - disc
        self.lbl.config(text="Último disco" if left == 0 else f"Faltan {left} disco{'s' if left > 1 else ''} más")
        self.cont.pack_forget()
        self.set(0)

    def set(self, frac, status=""):
        self.bar["value"] = max(0, min(1, frac)) * 1000
        self.note.config(text=NOTE + (f"\n{status}" if status else ""), fg=DIM)

    def wait(self, n, total):
        self.bar["value"] = 0
        self.lbl.config(text=f"Disco {n} de {total}")
        self.note.config(text=f"Inserta el disco {n}: la copia seguirá sola (o pulsa Continuar).", fg=ACCENT)
        self.cont.pack(side="right", padx=(0, 8))


class LaunchPopup(GamePopup):
    def __init__(self, app, m, mount, dest):
        super().__init__(app, m, mount, dest)
        self.m, self.dest, self.left = m, dest, LAUNCH_SECONDS
        self.count = tk.Label(self.body, font=(FAM, 10), bg=BG, fg=FG)
        self.count.pack(pady=(14, 0), anchor="w")
        btns = tk.Frame(self.body, bg=BG)
        btns.pack(fill="x", pady=(12, 0))
        button(btns, "Iniciar ahora", self.go, primary=True).pack(side="right")
        button(btns, "Cancelar", self.close).pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.close)
        present(self)
        self.tick()

    def tick(self):
        if self.left <= 0:
            return self.go()
        self.count.config(text=f"El juego se iniciará en {self.left} s…")
        self.left -= 1
        self.job = self.after(1000, self.tick)

    def go(self):
        self.app.launch(self.m, self.dest)
        self.close()


# ───────────────────────── aplicación ─────────────────────────
class App:
    def __init__(self):
        self.cfg = load_config()
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        setup_style(self.root)
        self.session = self.popup = self.settings = self.icon = None

    def post(self, fn, *a):
        self.q.put((fn, a))

    def pump(self):
        try:
            while True:
                fn, a = self.q.get_nowait()
                if isinstance(fn, str):          # eventos del vigilante de discos
                    fn, a = self.on_disc, a
                try:
                    fn(*a)
                except Exception as e:
                    self.notify(f"Error: {e}")
        except queue.Empty:
            pass
        self.root.after(200, self.pump)

    def run(self):
        import pystray
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((2, 2, 62, 62), fill=ACCENT)
        d.ellipse((19, 19, 45, 45), fill="white")
        d.ellipse((28, 28, 36, 36), fill=ACCENT)
        menu = pystray.Menu(
            pystray.MenuItem("Configuración", lambda i, it: self.post(self.show_settings), default=True),
            pystray.MenuItem("Salir", lambda i, it: self.post(self.quit)))
        self.icon = pystray.Icon(APP, im, APP, menu)
        self.icon.run_detached()
        threading.Thread(target=watcher, args=(lambda kind, mount: self.post(kind, mount),), daemon=True).start()
        self.root.after(200, self.pump)
        self.root.mainloop()

    def notify(self, msg, title=APP):
        try:
            if self.icon and self.icon.HAS_NOTIFICATION:
                self.icon.notify(msg, title)
        except Exception:
            pass

    def close_popup(self):
        if self.popup:
            self.popup.close()
            self.popup = None

    # ---- eventos de disco ----
    def on_disc(self, mount):
        m = read_manifest(mount)
        if not m:
            return
        s = self.session
        if s:
            if m["id"] == s["id"] and s["waiting"]:
                if m["disc"] == s["next"]:
                    self.start_copy(mount, m)
                else:
                    self.notify(f"Este es el disco {m['disc']}; inserta el disco {s['next']}.")
            return
        dest = Path(self.cfg["games_dir"]) / m["install_dir"]
        if dest.exists() and not (dest / PARTIAL).exists():
            self.close_popup()
            self.notify(f"{m['title']} se iniciará en {LAUNCH_SECONDS} segundos")
            self.popup = LaunchPopup(self, m, mount, dest)
        elif m["disc"] != 1:
            self.notify(f"Inserta el disco 1 de {m['title']} para instalarlo.")
        else:
            self.begin_install(mount, m, dest)

    def rescan(self):
        found = list(optical_discs())
        if not found and self.session:
            self.notify(f"No se detecta el disco {self.session['next']}.")
        for mount in found:
            self.on_disc(mount)

    # ---- instalación ----
    def begin_install(self, mount, m, dest):
        try:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)  # resto de una instalación incompleta
            dest.mkdir(parents=True)
            (dest / PARTIAL).write_text(m["id"])
        except OSError as e:
            return self.notify(f"No se pudo crear {dest}: {e}")
        self.session = dict(id=m["id"], dest=dest, next=1, waiting=False, cancel=threading.Event(), title=m["title"])
        self.close_popup()
        self.popup = InstallPopup(self, m, mount, dest)
        self.start_copy(mount, m)

    def start_copy(self, mount, m):
        s = self.session
        s["waiting"] = False
        self.popup.show_progress(m["disc"], m["total_discs"])
        s["thread"] = threading.Thread(target=self.worker, args=(mount, m, s), daemon=True)
        s["thread"].start()

    def worker(self, mount, m, s):
        last = [0.0]

        def report(f, text=""):
            if time.time() - last[0] > 0.1 or f >= 1:
                last[0] = time.time()
                self.post(self.on_progress, f, text)
        try:
            final = m["disc"] >= m["total_discs"]
            copy_disc(mount, s["dest"], s["cancel"], report, final)
            if not final:
                eject(mount)
            self.post(self.on_disc_done, m)
        except Cancelled:
            self.post(self.end_session, "Instalación cancelada; se eliminó lo copiado.")
        except Exception as e:
            self.post(self.end_session, f"Error al copiar: {e}")

    def on_progress(self, f, text):
        if self.session and self.popup:
            self.popup.set(f, text)

    def on_disc_done(self, m):
        s = self.session
        if not s:
            return
        if s["cancel"].is_set():
            return self.end_session("Instalación cancelada; se eliminó lo copiado.")
        if m["disc"] >= m["total_discs"]:
            (s["dest"] / PARTIAL).unlink(missing_ok=True)
            self.session = None
            self.close_popup()
            self.notify(f"{m['title']} se instaló correctamente.", "Copia completada")
        else:
            s["next"], s["waiting"] = m["disc"] + 1, True
            self.popup.wait(s["next"], m["total_discs"])
            self.notify(f"Inserta el disco {s['next']} de {m['total_discs']} para continuar.", m["title"])

    def cancel_install(self):
        s = self.session
        if not s or not messagebox.askyesno(APP, "¿Cancelar la copia? Se eliminará lo ya copiado.", parent=self.popup):
            return
        s["cancel"].set()
        if s["waiting"]:
            self.end_session("Instalación cancelada; se eliminó lo copiado.")

    def end_session(self, msg):
        s, self.session = self.session, None
        if s:
            shutil.rmtree(s["dest"], ignore_errors=True)
        self.close_popup()
        self.notify(msg)

    # ---- lanzar juego ----
    def launch(self, m, dest):
        try:
            exe = (dest / m["exe"]).resolve()
            if dest.resolve() not in exe.parents or not exe.exists():
                raise FileNotFoundError(m["exe"])
            cmd = [str(exe)]
            if not WIN:
                if exe.suffix.lower() == ".exe":
                    wine = shutil.which("wine")
                    if not wine:
                        raise RuntimeError("se necesita Wine para ejecutar .exe en Linux")
                    cmd = [wine, str(exe)]
                else:
                    os.chmod(exe, exe.stat().st_mode | 0o111)
            subprocess.Popen(cmd, cwd=exe.parent, start_new_session=not WIN, env=clean_env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.notify(f"No se pudo iniciar {m['title']}: {e}")

    # ---- configuración ----
    def show_settings(self):
        if self.settings and self.settings.winfo_exists():
            self.settings.deiconify()
            self.settings.lift()
            return
        w = self.settings = tk.Toplevel(self.root)
        w.title(f"{APP} · Configuración")
        w.configure(bg=BG)
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", w.withdraw)  # cerrar = esconder
        f = tk.Frame(w, bg=BG, padx=20, pady=16)
        f.pack()
        tk.Label(f, text="Configuración", font=(FAM, 13, "bold"), bg=BG, fg=ACCENT).pack(anchor="w")
        tk.Label(f, text="Carpeta de juegos", font=(FAM, 9), bg=BG, fg=FG).pack(anchor="w", pady=(14, 2))
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        path = tk.Label(row, text=self.cfg["games_dir"], font=(FAM, 8), bg=BG, fg=DIM, wraplength=260, justify="left")
        path.pack(side="left", fill="x", expand=True)

        def choose():
            d = filedialog.askdirectory(parent=w, initialdir=self.cfg["games_dir"])
            if d:
                self.cfg["games_dir"] = d
                save_config(self.cfg)
                path.config(text=d)
        button(row, "Cambiar…", choose).pack(side="right", padx=(10, 0))
        var = tk.BooleanVar(value=self.cfg["autostart"])

        def toggle():
            self.cfg["autostart"] = var.get()
            save_config(self.cfg)
            try:
                set_autostart(var.get())
            except Exception as e:
                messagebox.showerror(APP, str(e), parent=w)
        tk.Checkbutton(f, text="Iniciar con el sistema", variable=var, command=toggle, bg=BG, fg=FG,
                       activebackground=BG, selectcolor="white", font=(FAM, 9), bd=0,
                       highlightthickness=0).pack(anchor="w", pady=(14, 0))
        tk.Label(f, text="Cerrar esta ventana deja DiscBox activo en la bandeja.", font=(FAM, 8),
                 bg=BG, fg=DIM).pack(anchor="w", pady=(10, 0))
        present(w)

    def quit(self):
        s = self.session
        if s:
            if not messagebox.askyesno(APP, "Hay una copia en curso. ¿Salir y eliminar lo copiado?"):
                return
            s["cancel"].set()
            if s.get("thread"):
                s["thread"].join(5)
            shutil.rmtree(s["dest"], ignore_errors=True)
        try:
            self.icon.stop()
        finally:
            self.root.destroy()


def main():
    lock = socket.socket()  # una sola instancia
    try:
        lock.bind(("127.0.0.1", 47653))
    except OSError:
        return
    try:
        import pystray, PIL  # noqa: F401
    except ImportError:
        r = tk.Tk(); r.withdraw()
        messagebox.showerror(APP, "Faltan dependencias (ejecutando desde código): pip install pystray pillow")
        return
    App().run()


if __name__ == "__main__":
    main()
