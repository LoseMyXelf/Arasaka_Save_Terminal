"""Arasaka Save Terminal: a standalone Cyberpunk 2077 save utility."""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import threading
import textwrap
import webbrowser
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, simpledialog, ttk

from save_format import SaveContainer, SaveFormatError


ROOT = Path(__file__).resolve().parent
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
ASSET_ROOT = BUNDLE_ROOT / "assets"


COLORS = {
    "bg": "#050506",
    "sidebar": "#09090b",
    "surface": "#0d0b0e",
    "surface2": "#21191f",
    "surface3": "#301b22",
    "border": "#51242d",
    "text": "#f5eff0",
    "muted": "#b7a1a6",
    "dim": "#826c72",
    "cyan": "#ff394b",
    "cyan_dark": "#4b1720",
    "pink": "#ff6674",
    "amber": "#ff8790",
    "entry": "#1d1a20",
}


EDIT_LIMITS = {
    "cool": 20, "intelligence": 20, "reflexes": 20, "strength": 20, "technicalAbility": 20,
    "engineeringProgression": 60, "solo": 60, "shinobi": 60, "netrunner": 60, "headHunter": 60,
    "attributePoints": 120, "perkPoints": 120, "streetCred": 50, "level": 60,
    "money": 99_999_999,
}


def default_save_dir() -> Path:
    profile = os.environ.get("USERPROFILE")
    base = Path(profile) if profile else Path.home()
    return base / "Saved Games" / "CD Projekt Red" / "Cyberpunk 2077"


def read_save_header(save_path: Path) -> tuple[int, str, str]:
    """Read the small VASC header only; this does not decompress or alter a save."""
    with save_path.open("rb") as stream:
        raw = stream.read(25)
    if len(raw) != 25:
        raise ValueError("Save header is incomplete")
    # The packed time precedes the packed date in the on-disk header.
    magic, _save_version, game_version, reserved, packed_time, packed_date, _arch_version = struct.unpack(
        "<4sIIBIII", raw
    )
    if magic != b"VASC" or reserved != 0:
        raise ValueError("Unrecognized Cyberpunk save header")
    if packed_date & 1023:
        raise ValueError("Invalid packed save date")
    year, month, day = packed_date >> 20, ((packed_date >> 15) & 31) + 1, ((packed_date >> 10) & 31) + 1
    hour, minute = packed_time >> 22, (packed_time >> 16) & 63
    second, millis = (packed_time >> 10) & 63, packed_time & 1023
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 60 and millis < 1000):
        raise ValueError("Invalid packed save timestamp")
    return game_version, f"{year:04d}-{month:02d}-{day:02d}", f"{hour:02d}:{minute:02d}:{second:02d}.{millis:03d}"


@dataclass
class SaveEntry:
    folder: Path
    metadata: dict
    modified: float

    @property
    def data(self) -> dict:
        value = self.metadata.get("Data", {})
        return value.get("metadata", {}) if isinstance(value, dict) else {}

    @property
    def title(self) -> str:
        return self.folder.name.replace("-", " ")


class Card(tk.Canvas):
    """Rounded surface for a group of regular Tk widgets."""

    def __init__(self, master, *, fill=COLORS["surface"], radius=16, padding=16, **kwargs):
        super().__init__(master, background=master.cget("background"), highlightthickness=0,
                         borderwidth=0, **kwargs)
        self.fill = fill
        self.radius = radius
        self.padding = padding
        self.body = tk.Frame(self, background=fill, highlightthickness=0)
        self.window = self.create_window(padding, padding, anchor="nw", window=self.body)
        self.bind("<Configure>", self._draw)
        self._draw()

    def _draw(self, _event=None):
        width, height = max(2, self.winfo_width()), max(2, self.winfo_height())
        p = self.padding
        self.coords(self.window, p, p)
        self.itemconfigure(self.window, width=max(1, width - 2*p), height=max(1, height - 2*p))
        self.delete("surface")
        r = min(self.radius, width // 2, height // 2)
        points = [r, 0, width-r, 0, width, 0, width, r, width, height-r, width, height,
                  width-r, height, r, height, 0, height, 0, height-r, 0, r, 0, 0]
        self.create_polygon([1,1,width-r,1,width-1,r,width-1,height-1,r,height-1,1,height-r],
                            smooth=False, fill=self.fill, outline=COLORS["border"], tags="surface")
        self.tag_lower("surface")


class StartupSplash:
    """Timer-driven red logo reveal over the editor's main window."""

    def __init__(self, root, on_complete):
        self.root = root
        self.on_complete = on_complete
        self.canvas = tk.Canvas(root, background="#000000",
                                highlightthickness=0, borderwidth=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.canvas.bind("<Escape>", lambda _event: self._finish())
        self.emblem_ids = []
        self.label_id = None
        self.subtitle_id = None

    @staticmethod
    def _shade(step: int, count: int = 12) -> str:
        amount = max(0.0, min(1.0, step / count))
        start = (43, 8, 14)
        end = (255, 54, 70)
        rgb = tuple(round(a + (b - a) * amount) for a, b in zip(start, end))
        return "#%02x%02x%02x" % rgb

    def start(self):
        self.canvas.after(300, lambda: self._fade_logo(0))

    def _center(self):
        self.canvas.update_idletasks()
        return max(1, self.canvas.winfo_width()) // 2, max(1, self.canvas.winfo_height()) // 2

    def _draw_emblem(self, cx: int, cy: int, color: str):
        if self.emblem_ids:
            self.canvas.delete("emblem")
        r = 47
        ids = [self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, outline=color,
                                       width=5, tags="emblem")]
        # Three discs and their joined stem, drawn as native canvas vectors.
        ids.append(self.canvas.create_line(cx, cy-8, cx, cy+34, fill=color,
                                           width=8, capstyle="round", tags="emblem"))
        ids.append(self.canvas.create_line(cx, cy+3, cx-25, cy-10, fill=color,
                                           width=8, capstyle="round", tags="emblem"))
        ids.append(self.canvas.create_line(cx, cy+3, cx+25, cy-10, fill=color,
                                           width=8, capstyle="round", tags="emblem"))
        for px, py in ((cx, cy-22), (cx-25, cy-8), (cx+25, cy-8)):
            ids.append(self.canvas.create_oval(px-13, py-13, px+13, py+13,
                                               fill=color, outline=color, tags="emblem"))
        self.emblem_ids = ids

    def _fade_logo(self, step: int):
        if not self.canvas.winfo_exists():
            return
        color = self._shade(step)
        center_x, center_y = self._center()
        self._draw_emblem(center_x, center_y, color)
        if step < 12:
            self.canvas.after(92, lambda: self._fade_logo(step + 1))
        else:
            self.canvas.after(720, lambda: self._slide_and_reveal(0))

    def _slide_and_reveal(self, step: int):
        if not self.canvas.winfo_exists():
            return
        center_x, center_y = self._center()
        if step == 0:
            label_x = center_x - 72
            self.label_id = self.canvas.create_text(label_x, center_y - 10, text="ARASAKA",
                anchor="w", fill="#26080d", font=("Segoe UI Black", 38, "bold"),
                tags="brand-label")
            self.subtitle_id = self.canvas.create_text(label_x + 4, center_y + 34,
                text="ARASAKA SAVE TERMINAL  /  2077",
                anchor="w", fill="#26080d", font=("Segoe UI Semibold", 9),
                tags="brand-label")
            self.canvas.create_line(label_x, center_y + 58, label_x + 370, center_y + 58,
                                    fill="#381117", width=1, tags="brand-label")
        if step < 12:
            # Move far enough to leave a clear gap before the wordmark.
            self.canvas.move("emblem", -12, 0)
            self.canvas.itemconfigure(self.label_id, fill=self._shade(step))
            self.canvas.itemconfigure(self.subtitle_id, fill=self._shade(max(0, step - 2)))
            self.canvas.after(57, lambda: self._slide_and_reveal(step + 1))
        else:
            self.canvas.after(200, lambda: self._glitch(0))

    def _glitch(self, frame):
        if not self.canvas.winfo_exists():
            return
        self.canvas.delete("glitch")
        if frame >= 6:
            self.canvas.after(160, lambda: self._boot_console(0))
            return
        cx, cy = self._center()
        if frame % 2 == 0:
            for i in range(5):
                y = cy - 48 + i * 23
                shift = 9 if i % 2 else -12
                self.canvas.create_rectangle(cx-196+shift,y,cx+285+shift,y+3,
                    fill="black", outline="", tags="glitch")
                self.canvas.create_line(cx-190+shift,y+4,cx-115+shift,y+4,
                    fill=COLORS["cyan"],width=2,tags="glitch")
            self.canvas.create_text(cx,cy+94,text="SIGNAL SYNC // 2077",
                fill=COLORS["cyan"],font=("Consolas",9),tags="glitch")
        self.canvas.after(55, lambda: self._glitch(frame+1))

    def _boot_console(self, frame):
        """Decorative boot progress; no save operations run during this sequence."""
        if not self.canvas.winfo_exists():
            return
        if frame == 0:
            self.canvas.delete("all")
        self.canvas.delete("boot")
        cx, cy = self._center()
        left, right = cx-260, cx+260
        lines = ("ARASAKA // SAVE TERMINAL", "Initializing local archive interface...",
                 "Loading character and wallet modules...", "Backup and recovery controls ready.",
                 "Local workspace ready.  ACCESS GRANTED")
        thresholds = (0, 6, 12, 18, 30)
        for index, (line, threshold) in enumerate(zip(lines, thresholds)):
            if frame >= threshold:
                self.canvas.create_text(left,cy-100+index*36,anchor="w",
                    text=("> " if index else "")+line, fill=COLORS["cyan"],
                    font=("Consolas",12,"bold" if index==0 else "normal"),tags="boot")
        progress = min(frame / 30, 1.0)
        y = cy+98
        self.canvas.create_rectangle(left,y,right,y+18,outline=COLORS["cyan"],tags="boot")
        end = left+2+(right-left-4)*progress
        if progress:
            self.canvas.create_rectangle(left+2,y+2,end,y+16,fill=COLORS["cyan"],outline="",tags="boot")
        # Brief horizontal tears, cleared on the next frame; progress stays monotonic.
        if frame in (4,5,13,14,22,23):
            self.canvas.create_rectangle(left+2,y+7,end,y+11,fill="black",outline="",tags="boot")
            self.canvas.create_line(max(left+2,end-70),y+9,min(right-2,end+10),y+9,
                                    fill="#ffb2b8",width=2,tags="boot")
        self.canvas.create_text(right,y+35,anchor="e",text=f"INIT // {round(progress*100):03d}%",
                                fill=COLORS["cyan"],font=("Consolas",10),tags="boot")
        if frame < 30:
            self.canvas.after(50,lambda: self._boot_console(frame+1))
        else:
            self.canvas.after(450,self._finish)

    def _finish(self):
        if self.canvas.winfo_exists():
            self.canvas.destroy()
            self.on_complete()


def theme_titlebar(window):
    """Color the native Windows frame while retaining all window controls."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    window.update_idletasks()
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    hwnd = user32.GetAncestor(window.winfo_id(), 2)
    setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
    setter.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    # COLORREF stores the red component in the lowest byte.
    for attribute, number in ((20, 1), (34, 0x2d2451), (35, 0x0b0909), (36, 0x4b39ff)):
        value = wintypes.DWORD(number)
        setter(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))


class TerminalDialog(simpledialog.Dialog):
    """Modal terminal message. Closing a confirmation always cancels it."""

    def __init__(self, parent, kind, title, message):
        self.kind = kind
        self.heading = title
        self.message = message
        super().__init__(parent, title)

    def body(self, master):
        theme_titlebar(self)
        self.configure(background=COLORS["surface"])
        master.configure(background=COLORS["surface"], padx=15, pady=10)
        header = tk.Frame(master, background=COLORS["surface"])
        header.pack(fill="x")
        logo = getattr(self.parent, "_brand_logo", None)
        if logo:
            tk.Label(header, image=logo, background=COLORS["surface"]).pack(side="left", padx=(0, 10))
        tk.Label(header, text="ARASAKA  //  " + self.kind.upper(), font=("Consolas", 11, "bold"),
                 background=COLORS["surface"], foreground=COLORS["pink"]).pack(side="left")
        tk.Frame(master, background=COLORS["border"], height=1).pack(fill="x", pady=10)
        tk.Label(master, text=self.heading, anchor="w", font=("Segoe UI Semibold", 15),
                 background=COLORS["surface"], foreground=COLORS["text"]).pack(fill="x")
        area = tk.Frame(master, background=COLORS["surface"])
        area.pack(fill="both", expand=True, pady=(8, 0))
        body = tk.Text(area, width=50, height=2, wrap="word", relief="flat", borderwidth=0,
                       highlightthickness=0, background=COLORS["surface"], foreground=COLORS["text"],
                       font=("Segoe UI", 10), takefocus=False)
        body.pack(side="left", fill="both", expand=True)
        body.insert("1.0", self.message)
        body.configure(state="disabled")
        self.message_area = body
        return None

    def buttonbox(self):
        body = self.message_area
        lines = sum(max(1, len(textwrap.wrap(line, width=48))) for line in self.message.split("\n"))
        body.configure(height=min(12, max(2, lines)))
        if lines > 12:
            scroll = ttk.Scrollbar(body.master, command=body.yview)
            scroll.pack(side="right", fill="y")
            body.configure(yscrollcommand=scroll.set)
        bar = tk.Frame(self, background=COLORS["surface"])
        bar.pack(fill="x", padx=20, pady=(0, 14))
        confirm = self.kind == "Confirmation"
        accept = ttk.Button(bar, text="Yes" if confirm else "OK", style="Accent.TButton", command=self.ok)
        accept.pack(side="right")
        self.initial_focus = accept
        if confirm:
            cancel = ttk.Button(bar, text="Cancel", command=self.cancel)
            cancel.pack(side="right", padx=8)
            self.initial_focus = cancel
        def enter(event):
            (event.widget if isinstance(event.widget, ttk.Button) else self.initial_focus).invoke()
            return "break"
        self.bind("<Return>", enter)
        self.bind("<Escape>", self.cancel)

    def apply(self):
        self.result = True


class CyberpunkSaveEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.title("Arasaka Save Terminal")
        self.geometry("1380x880")
        self.minsize(1040, 680)
        self.configure(background=COLORS["bg"])
        self._set_window_icon()
        self.folder = default_save_dir()
        self.saves: list[SaveEntry] = []
        self.selected: SaveEntry | None = None
        self.character_values: dict[str, int] = {}
        self.character_entries: dict[str, ttk.Entry] = {}
        self.money_value: int | None = None
        self.photo = None
        self.active_tab = "overview"
        self._configure_style()
        self._build()
        self.refresh_saves()

    def _message(self, kind, title, message):
        return bool(TerminalDialog(self, kind, title, message).result)

    def show_centered(self):
        """Position once while hidden so the splash and editor never jump."""
        self.update_idletasks()
        left, top, right, bottom = 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):
                left, top, right, bottom = area.left, area.top, area.right, area.bottom
        # Leave space for the window frame and taskbar on smaller screens.
        width = min(1380, right - left - 24)
        height = min(880, bottom - top - 48)
        self.minsize(min(1040, width), min(680, height))
        x = left + (right - left - width) // 2
        y = top + max(0, (bottom - top - height - 30) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.update_idletasks()
        theme_titlebar(self)
        self.deiconify()

    def _set_window_icon(self):
        png_path = ASSET_ROOT / "arasaka_logo.png"
        ico_path = ASSET_ROOT / "arasaka_logo.ico"
        try:
            self._icon_photo = tk.PhotoImage(file=str(png_path))
            self.iconphoto(True, self._icon_photo)
        except (OSError, tk.TclError):
            self._icon_photo = None
        if os.name == "nt" and ico_path.is_file():
            try:
                # Set this toplevel's icon explicitly; ``default=`` only sets
                # Tk's fallback for future windows and may leave the Tk feather
                # on the root window's title bar/taskbar button.
                self.iconbitmap(str(ico_path))
            except tk.TclError:
                pass

    def _configure_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Sidebar.TFrame", background=COLORS["sidebar"])
        style.configure("Card.TFrame", background=COLORS["surface"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("CardMuted.TLabel", background=COLORS["surface"], foreground=COLORS["muted"], font=("Segoe UI", 10))
        style.configure("Accent.TLabel", background=COLORS["surface"], foreground=COLORS["cyan"], font=("Segoe UI Semibold", 10))
        style.configure("Hero.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI Semibold", 25))
        style.configure("Section.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI Semibold", 14))
        style.configure("Sidebar.TLabel", background=COLORS["sidebar"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("SidebarMuted.TLabel", background=COLORS["sidebar"], foreground=COLORS["muted"], font=("Segoe UI", 10))
        style.configure("TButton", background=COLORS["surface2"], foreground=COLORS["text"], borderwidth=0,
                        padding=(12, 8), font=("Segoe UI Semibold", 10))
        style.map("TButton", background=[("active", COLORS["surface3"]), ("disabled", COLORS["surface"])],
                  foreground=[("disabled", COLORS["dim"])])
        style.configure("Accent.TButton", background=COLORS["cyan_dark"], foreground=COLORS["cyan"])
        style.map("Accent.TButton", background=[("active", "#68222c")])
        style.configure("TEntry", fieldbackground=COLORS["entry"], foreground=COLORS["text"],
                        insertcolor=COLORS["cyan"], padding=8, borderwidth=1,
                        bordercolor=COLORS["border"], lightcolor=COLORS["border"],
                        darkcolor=COLORS["border"], relief="flat",
                        selectbackground=COLORS["cyan_dark"], selectforeground=COLORS["text"])
        style.map("TEntry",
                  bordercolor=[("focus", COLORS["cyan"]), ("!focus", COLORS["border"])],
                  lightcolor=[("focus", COLORS["cyan"]), ("!focus", COLORS["border"])],
                  darkcolor=[("focus", COLORS["cyan"]), ("!focus", COLORS["border"])],
                  fieldbackground=[("disabled", COLORS["surface"])],
                  foreground=[("disabled", COLORS["dim"])])
        style.configure("Treeview", background=COLORS["surface2"], fieldbackground=COLORS["surface2"],
                        foreground=COLORS["text"], rowheight=32, borderwidth=0,
                        font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=COLORS["surface3"], foreground=COLORS["cyan"],
                        relief="flat", font=("Segoe UI Semibold", 10))
        style.map("Treeview", background=[("selected", COLORS["cyan_dark"])],
                  foreground=[("selected", COLORS["cyan"])])
        style.map("Treeview.Heading", background=[("active", COLORS["surface3"])])
        style.configure("Compact.Treeview", rowheight=25, background=COLORS["surface"], fieldbackground=COLORS["surface"], foreground=COLORS["text"], borderwidth=0)
        style.layout("Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Vertical.TScrollbar", background="#762737", troughcolor=COLORS["sidebar"],
                        bordercolor=COLORS["sidebar"], lightcolor="#762737", darkcolor="#762737",
                        borderwidth=0, width=10, arrowsize=10, relief="flat", gripcount=0)
        style.map("Vertical.TScrollbar",
                  background=[("pressed", COLORS["cyan"]), ("active", COLORS["pink"])],
                  lightcolor=[("pressed", COLORS["cyan"]), ("active", COLORS["pink"])],
                  darkcolor=[("pressed", COLORS["cyan"]), ("active", COLORS["pink"])])

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = tk.Frame(self, background=COLORS["sidebar"], width=276, padx=18, pady=18)
        self.sidebar.grid(row=0, column=2, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(4, weight=1)
        brand = tk.Frame(self.sidebar, background=COLORS["sidebar"])
        brand.grid(row=0, column=0, sticky="ew", pady=(2, 28))
        logo_path = ASSET_ROOT / "arasaka_logo.png"
        try:
            self._brand_logo = tk.PhotoImage(file=str(logo_path)).subsample(5, 5)
            tk.Label(brand, image=self._brand_logo, background=COLORS["sidebar"],
                     width=48, height=48).pack(side="left", padx=(0, 8))
        except (OSError, tk.TclError):
            tk.Label(brand, text="NC", background=COLORS["cyan"], foreground=COLORS["bg"],
                     font=("Segoe UI Black", 13), width=3, height=1, padx=2, pady=5).pack(side="left")
        tk.Label(brand, text="  ARASAKA\n  SAVE TERMINAL", justify="left", background=COLORS["sidebar"],
                 foreground=COLORS["text"], font=("Segoe UI Semibold", 10), pady=2).pack(side="left")
        ttk.Label(self.sidebar, text="SAVE ARCHIVE // LOCAL", style="SidebarMuted.TLabel").grid(row=1, column=0, sticky="w")
        self.save_count = ttk.Label(self.sidebar, text="Scanning…", style="SidebarMuted.TLabel")
        self.save_count.grid(row=2, column=0, sticky="w", pady=(4, 10))
        self.save_list = tk.Listbox(self.sidebar, background=COLORS["sidebar"], foreground=COLORS["text"],
                                    selectbackground=COLORS["cyan_dark"], selectforeground=COLORS["cyan"],
                                    activestyle="none", borderwidth=0, highlightthickness=0,
                                    font=("Segoe UI Semibold", 10), selectmode="browse")
        self.save_scroll = ttk.Scrollbar(self.sidebar, orient="vertical", command=self.save_list.yview)
        self.save_list.configure(yscrollcommand=self.save_scroll.set)
        self.save_list.grid(row=4, column=0, sticky="nsew")
        self.save_scroll.grid(row=4, column=1, sticky="ns")
        self.save_list.bind("<<ListboxSelect>>", self._select_from_list)
        self.sidebar_actions = tk.Frame(self.sidebar, background=COLORS["sidebar"])
        self.sidebar_actions.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(self.sidebar_actions, text="Choose save folder", command=self.choose_folder).pack(fill="x")
        ttk.Button(self.sidebar_actions, text="Refresh", command=self.refresh_saves).pack(fill="x", pady=(7, 0))
        self.folder_hint = ttk.Label(self.sidebar, text=str(self.folder), style="SidebarMuted.TLabel", wraplength=228)
        self.folder_hint.grid(row=6, column=0, sticky="sw", pady=(14, 0))

        self.main = tk.Frame(self, background=COLORS["bg"], padx=16, pady=22)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(3, weight=1)
        top = tk.Frame(self.main, background=COLORS["bg"])
        top.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        top.grid_columnconfigure(0, weight=1)
        ttk.Label(top, text="SAVE WORKSHOP  /  CYBERPUNK 2077", style="Accent.TLabel").grid(row=0, column=0, sticky="w")
        self.status = ttk.Label(top, text="Local saves only", style="Muted.TLabel")
        self.status.grid(row=0, column=1, sticky="e")
        self.signal_art = tk.Canvas(top, width=116, height=26, background=COLORS["bg"],
                                    highlightthickness=0, borderwidth=0)
        self.signal_art.grid(row=0, column=2, sticky="e", padx=(18, 0))
        self._draw_signal_art()

        self.title_label = ttk.Label(self.main, text="Your story, at a glance.", style="Hero.TLabel")
        self.title_label.grid(row=1, column=0, sticky="w")
        self.subtitle = ttk.Label(self.main, text="Select a save slot to inspect its profile and save version.", style="Muted.TLabel")
        self.subtitle.grid(row=2, column=0, sticky="w", pady=(5, 17))

        rail = tk.Frame(self, background=COLORS["sidebar"], width=94,
                        highlightbackground=COLORS["border"], highlightthickness=1)
        rail.grid(row=0, column=0, sticky="ns")
        rail.grid_propagate(False)
        self.tab_bar = tk.Canvas(rail, width=90, background=COLORS["sidebar"], highlightthickness=0)
        self.tab_bar.pack(fill="both", expand=True)
        self.tab_bar.bind("<Configure>", self._draw_tabs)
        self.tab_bar.bind("<Button-1>", self._tab_click)
        controls = tk.Frame(self.main, background=COLORS["bg"])
        controls.grid(row=3,column=0,sticky="ew",pady=(0,14))
        for label, command in (("OPEN FOLDER",self.choose_folder),("REFRESH",self.refresh_saves),
                               ("BACKUP",self.backup_selected_save),("RESTORE",self.restore_selected_save)):
            ttk.Button(controls,text=label,command=command).pack(side="left",padx=(0,7))
        self.content = tk.Frame(self.main, background=COLORS["bg"])
        self.content.grid(row=4, column=0, sticky="nsew")
        self.main.grid_rowconfigure(3, weight=0)
        self.main.grid_rowconfigure(4, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.overview_page = tk.Frame(self.content, background=COLORS["bg"])
        self.profile_page = tk.Frame(self.content, background=COLORS["bg"])
        self.technical_page = tk.Frame(self.content, background=COLORS["bg"])
        self.about_page = tk.Frame(self.content, background=COLORS["bg"])
        for page in (self.overview_page, self.profile_page, self.technical_page, self.about_page):
            page.grid(row=0, column=0, sticky="nsew")
        for page in (self.overview_page, self.profile_page, self.technical_page, self.about_page):
            self._watermark(page)
        self._build_overview()
        self._build_profile()
        self._build_technical()
        self._build_about()
        self._show_tab("overview")

    def _watermark(self, parent):
        canvas = tk.Canvas(parent, background=COLORS["bg"], highlightthickness=0)
        canvas.place(x=0,y=0,relwidth=1,relheight=1)
        canvas.tk.call("lower",canvas._w)
        def draw(event):
            canvas.delete("all")
            x,y=event.width//2,event.height-100
            color="#281017"
            canvas.create_oval(x-205,y-48,x-109,y+48,outline=color,width=4)
            canvas.create_line(x-157,y-20,x-157,y+33,fill=color,width=7)
            canvas.create_line(x-184,y-8,x-157,y+13,x-130,y-8,fill=color,width=7)
            for px,py in ((x-157,y-24),(x-184,y-8),(x-130,y-8)):
                canvas.create_oval(px-13,py-13,px+13,py+13,fill=color,outline=color)
            canvas.create_text(x-85,y-5,text="ARASAKA",anchor="w",fill=color,font=("Segoe UI Black",38,"bold"))
            canvas.create_text(x-81,y+35,text="SAVE TERMINAL / LOCAL ARCHIVE",anchor="w",fill=color,font=("Consolas",10))
        canvas.bind("<Configure>",draw)

    def _draw_signal_art(self):
        """Static scanline/glitch accents; no animation or recurring work."""
        self.signal_art.delete("all")
        self.signal_art.create_line(0, 13, 116, 13, fill=COLORS["border"], width=1)
        for x, y, width, color in (
            (8, 6, 21, COLORS["cyan"]), (36, 6, 9, COLORS["pink"]),
            (54, 17, 26, COLORS["cyan_dark"]), (88, 6, 12, COLORS["pink"]),
            (103, 17, 8, COLORS["cyan"]),
        ):
            self.signal_art.create_rectangle(x, y, x + width, y + 2, fill=color, outline="")

    def _build_overview(self):
        page = self.overview_page
        page.grid_columnconfigure(0, weight=3, uniform="overview")
        page.grid_columnconfigure(1, weight=2, uniform="overview")
        page.grid_rowconfigure(1, weight=1)

        self.metric_values: dict[str, tk.Label] = {}
        metrics = tk.Frame(page, background=COLORS["bg"])
        metrics.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 13))
        for col, (key, label) in enumerate((
            ("level", "LEVEL"), ("streetCred", "STREET CRED"), ("playTime", "PLAY TIME"), ("lifePath", "LIFE PATH"),
        )):
            metrics.grid_columnconfigure(col, weight=1, uniform="metric")
            card = Card(metrics, height=92, fill=COLORS["surface"], padding=12)
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            tk.Label(card.body, text=label, background=COLORS["surface"], foreground=COLORS["muted"],
                     font=("Segoe UI Semibold", 10)).pack(anchor="w")
            val = tk.Label(card.body, text="—", background=COLORS["surface"], foreground=COLORS["text"],
                           font=("Segoe UI Semibold", 16))
            val.pack(anchor="w", pady=(6, 0))
            self.metric_values[key] = val

        cover = Card(page, fill=COLORS["surface"], padding=0)
        cover.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        cover.body.configure(padx=0, pady=0)
        self.cover = tk.Label(cover.body, background=COLORS["surface2"], foreground=COLORS["dim"],
                              text="SELECT A SAVE TO VIEW ITS SCREENSHOT", font=("Segoe UI Semibold", 9))
        self.cover.pack(fill="both", expand=True)
        self.cover.bind("<Configure>", self._fit_cover)

        details = tk.Frame(page, background=COLORS["bg"])
        details.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        details.grid_columnconfigure(0, weight=1)
        summary = Card(details, fill=COLORS["surface"], padding=18)
        summary.grid(row=0, column=0, sticky="ew")
        tk.Label(summary.body, text="SLOT PROFILE", background=COLORS["surface"], foreground=COLORS["cyan"],
                 font=("Segoe UI Semibold", 10)).pack(anchor="w")
        self.profile_name = tk.Label(summary.body, text="No save selected", background=COLORS["surface"],
                                     foreground=COLORS["text"], font=("Segoe UI Semibold", 20))
        self.profile_name.pack(anchor="w", pady=(8, 3))
        self.profile_info = tk.Label(summary.body, text="Choose one of your saves on the left.", justify="left",
                                     background=COLORS["surface"], foreground=COLORS["muted"],
                                     font=("Segoe UI", 11), wraplength=350)
        self.profile_info.pack(anchor="w", pady=(0, 16))
        self.copy_button = ttk.Button(summary.body, text="Create full save copy…", style="Accent.TButton",
                                      command=self.create_safety_copy, state="disabled")
        self.copy_button.pack(anchor="w")

        info = Card(details, fill=COLORS["surface"], padding=18)
        info.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        tk.Label(info.body, text="SAVE FILE STATUS", background=COLORS["surface"], foreground=COLORS["cyan"],
                 font=("Segoe UI Semibold", 10)).pack(anchor="w")
        self.parser_status = tk.Label(info.body, text="No save loaded", background=COLORS["surface"],
                                      foreground=COLORS["muted"], justify="left", wraplength=350,
                                      font=("Segoe UI", 10))
        self.parser_status.pack(anchor="w", pady=(8, 0))

        safe = Card(details, fill=COLORS["surface2"], padding=15)
        safe.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        tk.Label(safe.body, text="SAFE MODE", background=COLORS["surface2"], foreground=COLORS["amber"],
                 font=("Segoe UI Semibold", 10)).pack(anchor="w")
        tk.Label(safe.body, text="Edits will target a separate copy. Restore requires confirmation and preserves the current file first.",
                 background=COLORS["surface2"], foreground=COLORS["muted"], justify="left", wraplength=360,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 0))
        recovery = Card(details, fill=COLORS["surface"], padding=15)
        recovery.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        tk.Label(recovery.body, text="SAVE RECOVERY", background=COLORS["surface"],
                 foreground=COLORS["cyan"], font=("Segoe UI Semibold", 10)).pack(anchor="w")
        tk.Label(recovery.body, text="Keep a restorable copy of this slot’s sav.dat before editing.",
                 background=COLORS["surface"], foreground=COLORS["muted"], wraplength=360,
                 justify="left", font=("Segoe UI", 10)).pack(anchor="w", pady=(5, 10))
        recovery_buttons = tk.Frame(recovery.body, background=COLORS["surface"])
        recovery_buttons.pack(anchor="w")
        self.backup_save_button = ttk.Button(recovery_buttons, text="Back up sav.dat",
                                             command=self.backup_selected_save, state="disabled")
        self.backup_save_button.pack(side="left")
        self.restore_save_button = ttk.Button(recovery_buttons, text="Restore backup",
                                              command=self.restore_selected_save, state="disabled")
        self.restore_save_button.pack(side="left", padx=(8, 0))

    def _build_profile(self):
        page = self.profile_page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        title = ttk.Label(page, text="Character editor", style="Section.TLabel")
        title.grid(row=0, column=0, sticky="w", pady=(2, 12))
        scroll_host = tk.Frame(page, background=COLORS["bg"])
        scroll_host.grid(row=1, column=0, sticky="nsew")
        scroll_host.grid_columnconfigure(0, weight=1)
        scroll_host.grid_rowconfigure(0, weight=1)
        self.profile_canvas = tk.Canvas(scroll_host, background=COLORS["bg"], highlightthickness=0, borderwidth=0)
        self.profile_canvas.grid(row=0, column=0, sticky="nsew")
        profile_scroll = ttk.Scrollbar(scroll_host, orient="vertical", command=self.profile_canvas.yview)
        profile_scroll.grid(row=0, column=1, sticky="ns")
        self.profile_canvas.configure(yscrollcommand=profile_scroll.set)
        self.bind_all("<MouseWheel>", self._profile_mousewheel, add="+")
        self.bind_all("<Button-4>", lambda event: self._profile_mousewheel_linux(event, -1), add="+")
        self.bind_all("<Button-5>", lambda event: self._profile_mousewheel_linux(event, 1), add="+")
        self.profile_content = tk.Frame(self.profile_canvas, background=COLORS["bg"])
        self.profile_window = self.profile_canvas.create_window((0, 0), window=self.profile_content, anchor="nw")
        self.profile_content.bind("<Configure>", lambda _e: self.profile_canvas.configure(
            scrollregion=self.profile_canvas.bbox("all")))
        self.profile_canvas.bind("<Configure>", lambda e: self.profile_canvas.itemconfigure(
            self.profile_window, width=e.width))
        self.profile_card = Card(self.profile_content, fill=COLORS["surface"], padding=22, height=560)
        self.profile_card.grid(row=0, column=0, sticky="ew")
        self.profile_content.grid_columnconfigure(0, weight=1)
        self.profile_fields = tk.Frame(self.profile_card.body, background=COLORS["surface"])
        self.profile_fields.pack(fill="x")
        wallet = tk.Frame(self.profile_fields, background=COLORS["surface"])
        self.wallet_panel = wallet
        wallet.grid(row=7, column=2, columnspan=2, rowspan=5, sticky="new", padx=(0,20))
        tk.Label(wallet, text="WALLET", background=COLORS["surface"], foreground=COLORS["cyan"],
                 font=("Segoe UI Semibold", 10)).pack(anchor="w")
        self.money_balance = tk.Label(wallet, text="Reading save…", background=COLORS["surface"],
                                      foreground=COLORS["text"], font=("Segoe UI Semibold", 20))
        self.money_balance.pack(anchor="w", pady=(6, 12))
        editor_row = tk.Frame(wallet, background=COLORS["surface"])
        editor_row.pack(fill="x")
        tk.Label(editor_row, text="New balance", background=COLORS["surface"], foreground=COLORS["muted"],
                 font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(0,4))
        self.money_entry = self._limited_entry(editor_row, EDIT_LIMITS["money"], width=24)
        self.money_entry.pack(fill="x")
        save_bar = tk.Frame(page, background=COLORS["surface3"], padx=12, pady=10,
                            highlightbackground=COLORS["cyan"], highlightthickness=1)
        save_bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.money_export_button = ttk.Button(save_bar, text="SAVE COPY",
                                              style="Accent.TButton", command=self.export_character_copy,
                                              state="disabled")
        self.money_export_button.pack(side="right", padx=(12, 0))
        tk.Label(save_bar, text="Save all character and money changes\nCreates a separate save copy with a restore backup.",
                 background=COLORS["surface3"], foreground=COLORS["text"],
                 justify="left", font=("Segoe UI", 9)).pack(side="left")
        self.money_status = tk.Label(wallet, text="Original save files remain untouched. Exports include a backup.",
                                     background=COLORS["surface"], foreground=COLORS["muted"],
                                     font=("Segoe UI", 9), wraplength=280, justify="left")
        self.money_status.pack(anchor="w", pady=(9, 0))
        self.profile_disclaimer = tk.Label(self.profile_content, text="Only the listed attributes, five skill progressions, point pools, Level, Street Cred, and wallet are editable. Skill XP stays unchanged. Exports are separate copies; test them in-game before relying on them.",
                                           background=COLORS["bg"], foreground=COLORS["muted"],
                                           font=("Segoe UI", 10), wraplength=900, justify="left")
        self.profile_disclaimer.grid(row=2, column=0, sticky="w", pady=(12, 0))

    def _limited_entry(self, parent, maximum: int, *, width=12):
        value = tk.StringVar(master=parent)
        entry = ttk.Entry(parent, width=width, textvariable=value)
        validate = (self.register(lambda proposed:
                                  proposed == "" or (proposed.isascii() and proposed.isdecimal())), "%P")
        entry.configure(validate="key", validatecommand=validate)
        entry._limit_validatecommand = validate
        ceiling = str(maximum)
        pending = None

        def cancel_pending(*_):
            nonlocal pending
            if pending is not None:
                entry.after_cancel(pending)
                pending = None

        def clamp(*_):
            cancel_pending()
            # Loading existing values deliberately suspends validation. Only
            # user edits are clamped; export validation remains the final guard.
            if str(entry.cget("validate")) == "none":
                return
            text = value.get()
            if not text or not text.isascii() or not text.isdecimal():
                return
            digits = text.lstrip("0") or "0"
            # Compare digits without converting arbitrarily long pasted input.
            if len(digits) > len(ceiling) or (len(digits) == len(ceiling) and digits > ceiling):
                value.set(ceiling)
                entry.icursor("end")

        def schedule_clamp(*_):
            nonlocal pending
            cancel_pending()
            if str(entry.cget("validate")) != "none":
                pending = entry.after(650, clamp)

        value.trace_add("write", schedule_clamp)
        entry.bind("<FocusOut>", clamp, add="+")
        entry.bind("<Return>", clamp, add="+")
        entry.bind("<Destroy>", cancel_pending, add="+")
        entry._limit_value = value
        return entry

    def _profile_mousewheel(self, event):
        if self.active_tab != "profile" or not self.profile_canvas.winfo_ismapped():
            return
        x, y = event.x_root, event.y_root
        left, top = self.profile_canvas.winfo_rootx(), self.profile_canvas.winfo_rooty()
        if left <= x < left + self.profile_canvas.winfo_width() and top <= y < top + self.profile_canvas.winfo_height():
            steps = -1 if event.delta > 0 else 1
            self.profile_canvas.yview_scroll(steps, "units")
            return "break"

    def _profile_mousewheel_linux(self, event, direction: int):
        if self.active_tab == "profile" and self.profile_canvas.winfo_ismapped():
            x, y = event.x_root, event.y_root
            left, top = self.profile_canvas.winfo_rootx(), self.profile_canvas.winfo_rooty()
            if left <= x < left + self.profile_canvas.winfo_width() and top <= y < top + self.profile_canvas.winfo_height():
                self.profile_canvas.yview_scroll(direction, "units")
                return "break"

    def _build_about(self):
        page = self.about_page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        ttk.Label(page, text="ABOUT // ARASAKA SAVE TERMINAL", style="Section.TLabel").grid(
            row=0,column=0,sticky="w",pady=(4,14))
        text = tk.Text(page, wrap="word", background=COLORS["surface"], foreground=COLORS["text"],
                       font=("Segoe UI",11), padx=20,pady=18, borderwidth=0,
                       highlightthickness=1,highlightbackground=COLORS["border"])
        text.grid(row=1,column=0,sticky="nsew")
        scroll=ttk.Scrollbar(page,orient="vertical",command=text.yview)
        scroll.grid(row=1,column=1,sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        text.tag_configure("heading",foreground=COLORS["cyan"],font=("Segoe UI",12,"bold"),spacing1=12,spacing3=6)
        sections = (
            ("UNOFFICIAL FAN PROJECT", "This is an unofficial fan work and is not approved or endorsed by CD PROJEKT RED. Arasaka Save Terminal is an independently developed save utility for Cyberpunk 2077.\n\n"),
            ("OWNERSHIP & CREDITS", "Cyberpunk 2077, Arasaka names and imagery, and game-derived logos and artwork belong to CD PROJEKT RED and/or their respective rights holders. No ownership of those materials is claimed by this project. Original editor code is separate from those game assets.\n\n"),
            ("FAN CONTENT & PERMISSIONS", "Use of game content is subject to CD PROJEKT RED's Fan Content Guidelines and applicable agreements. These guidelines are conditional, not individual approval of this editor. They require advance permission for standalone products using their content. This notice does not establish permission or grant asset redistribution rights.\n\n"),
            ("SAVE FILES", "Keep an original backup before editing. Edited copies may behave differently across game versions and mods. The About page does not change your saves.\n\n"),
            ("OFFICIAL REFERENCES", "Use the links below for the publisher's current terms. Reviewed 30 October 2026.\n"),
        )
        for title, body in sections:
            text.insert("end",title+"\n","heading")
            text.insert("end",body)
        text.configure(state="disabled")
        links=tk.Frame(page,background=COLORS["bg"])
        links.grid(row=2,column=0,sticky="ew",pady=12)
        for label,url in (("FAN CONTENT GUIDELINES","https://www.cdprojektred.com/en/fan-content"),
                          ("USER AGREEMENT","https://regulations.cdprojektred.com/en/user_agreement"),
                          ("CYBERPUNK 2077 EULA","https://store.steampowered.com/eula/1091500_eula_0?eulaLang=english")):
            ttk.Button(links,text=label,command=lambda link=url:webbrowser.open(link)).pack(side="left",padx=(0,8))

    def _build_technical(self):
        page = self.technical_page
        page.grid_columnconfigure(0, weight=1)
        ttk.Label(page, text="SAVE DATA // ARCHIVE INSPECTOR", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(2, 4))
        ttk.Label(page, text="Readable slot summary and file diagnostics. These values do not directly edit the game state.",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 10))
        page.grid_rowconfigure(2, weight=1)
        self.technical_card = Card(page, fill=COLORS["surface"], padding=12)
        self.technical_card.grid(row=2, column=0, sticky="nsew")
        self.technical_card.body.grid_columnconfigure(0, weight=1)
        self.technical_card.body.grid_rowconfigure(0, weight=1)
        fields_wrap = tk.Frame(self.technical_card.body, background=COLORS["surface"])
        fields_wrap.grid(row=0, column=0, sticky="nsew")
        fields_wrap.grid_columnconfigure(0, weight=1)
        fields_wrap.grid_rowconfigure(0, weight=1)
        self.technical_tree = ttk.Treeview(fields_wrap, columns=("value",), show="tree headings", height=14, style="Compact.Treeview")
        self.technical_tree.tag_configure("group", foreground=COLORS["cyan"], background=COLORS["surface3"])
        self.technical_tree.heading("#0", text="FIELD", anchor="w")
        self.technical_tree.heading("value", text="VALUE", anchor="w")
        self.technical_tree.column("#0", width=230, stretch=False)
        self.technical_tree.column("value", width=380, stretch=True)
        self.technical_tree.grid(row=0, column=0, sticky="nsew")
        technical_scroll = ttk.Scrollbar(fields_wrap, orient="vertical", command=self.technical_tree.yview)
        technical_scroll.grid(row=0, column=1, sticky="ns")
        self.technical_tree.configure(yscrollcommand=technical_scroll.set)
        tk.Label(page, text="Read-only slot metadata. Use CHARACTER to edit supported values in sav.dat.",
                 background=COLORS["bg"], foreground=COLORS["muted"], font=("Segoe UI", 10),
                 wraplength=900, justify="left").grid(row=3, column=0, sticky="w", pady=(12, 0))

    def _draw_tabs(self, _event=None):
        if not hasattr(self,"tab_bar"):
            return
        canvas=self.tab_bar
        canvas.delete("all")
        canvas.create_text(45,38,text="NC / 77",fill=COLORS["cyan"],font=("Consolas",12,"bold"))
        canvas.create_line(12,62,78,62,fill=COLORS["cyan"],width=2)
        self._tab_targets=[]
        for i,(key,title) in enumerate((("overview","OVERVIEW"),("profile","CHARACTER"),("technical","SAVE DATA"),("about","ABOUT"))):
            y=148+i*94
            active=key==self.active_tab
            canvas.create_polygon(8,y,70,y,82,y+12,82,y+78,8,y+78,
                fill=COLORS["cyan_dark"] if active else COLORS["surface"],
                outline=COLORS["cyan"] if active else COLORS["border"])
            canvas.create_text(45,y+25,text=f"0{i+1}",font=("Consolas",18,"bold"),
                fill=COLORS["cyan"] if active else COLORS["muted"])
            canvas.create_text(45,y+57,text=title,font=("Segoe UI",8,"bold"),
                fill=COLORS["text"] if active else COLORS["muted"])
            self._tab_targets.append((key,y,y+78))
        canvas.create_text(45,max(560,canvas.winfo_height()-38),text="LOCAL\nACCESS",
            fill=COLORS["dim"],font=("Consolas",8),justify="center")

    def _tab_click(self,event):
        for key,top,bottom in getattr(self,"_tab_targets",[]):
            if 8<=event.x<=82 and top<=event.y<=bottom:
                self._show_tab(key)
                return

    def _show_tab(self, key: str):
        self.active_tab = key
        for page in (self.overview_page, self.profile_page, self.technical_page, self.about_page):
            page.grid_remove()
        {"overview": self.overview_page, "profile": self.profile_page,
         "technical": self.technical_page, "about": self.about_page}[key].grid()
        self._draw_tabs()

    def choose_folder(self):
        selected = filedialog.askdirectory(initialdir=self.folder if self.folder.exists() else Path.home())
        if selected:
            self.folder = Path(selected)
            self.folder_hint.configure(text=str(self.folder))
            self.refresh_saves()

    def refresh_saves(self):
        self.save_list.delete(0, "end")
        self.saves.clear()
        self.selected = None
        if not self.folder.is_dir():
            self.save_count.configure(text="Folder not found")
            self.status.configure(text="Choose the Cyberpunk 2077 save directory")
            self._render_save(None)
            return
        for folder in self.folder.iterdir():
            if not folder.is_dir() or not (folder / "sav.dat").is_file():
                continue
            metadata = {}
            meta_path = folder / "metadata.9.json"
            try:
                metadata = json.loads(meta_path.read_text(encoding="utf-8-sig")) if meta_path.is_file() else {}
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
            try:
                modified = (folder / "sav.dat").stat().st_mtime
            except OSError:
                modified = 0
            self.saves.append(SaveEntry(folder, metadata, modified))
        self.saves.sort(key=lambda save: save.modified, reverse=True)
        self.save_count.configure(text=f"{len(self.saves)} slots detected")
        for entry in self.saves:
            profile = entry.data
            level = self._format_number(profile.get("level"))
            life = profile.get("lifePath", "Unknown path")
            self.save_list.insert("end", f"{entry.title}   ·   LV {level}   ·   {life}")
        self.status.configure(text=f"{len(self.saves)} local save slots  ·  Originals protected")
        if self.saves:
            self.save_list.selection_clear(0, "end")
            self.save_list.selection_set(0)
            self.save_list.activate(0)
            self.save_list.see(0)
            self.after_idle(self._select_from_list)
        else:
            self._render_save(None)

    def _select_from_list(self, _event=None):
        selection = self.save_list.curselection()
        if selection and selection[0] < len(self.saves):
            self._select_entry(self.saves[selection[0]])

    def _select_entry(self, entry: SaveEntry):
        self.selected = entry
        self.character_values = {}
        self.money_value = None
        self._render_save(entry)
        self.money_balance.configure(text="Reading save…")
        self.money_status.configure(text="Finding the saved wallet value…")
        self.money_entry.delete(0, "end")
        self.money_export_button.configure(state="disabled")
        self.copy_button.configure(state="normal")
        self.backup_save_button.configure(state="normal")
        self.restore_save_button.configure(
            state="normal" if self._save_backup_path(entry).is_file() else "disabled")
        self.parser_status.configure(text="Checking save header…")
        threading.Thread(target=self._read_header, args=(entry,), daemon=True).start()

    def _read_header(self, entry: SaveEntry):
        try:
            version, date, time = read_save_header(entry.folder / "sav.dat")
            container = SaveContainer(entry.folder / "sav.dat")
            try:
                money = container.money()
            except Exception:
                money = None
            try:
                character = container.character_snapshot()
            except Exception:
                character = None
            result = (f"Recognized · game version {version / 1000:g}",
                      f"Save timestamp: {date}  {time}\nSave file: {entry.folder / 'sav.dat'}", money, character)
        except Exception as exc:
            result = ("Save data not fully recognized", str(exc), None, None)
        self.after(0, lambda: self._apply_header(entry, result))

    def _apply_header(self, entry: SaveEntry, result):
        if self.selected is not entry:
            return
        title, detail, money, character = result
        self.parser_status.configure(text=f"{title}\n{detail}")
        self.character_values = character.values.copy() if character else {}
        self.money_value = money
        self._render_profile_fields(entry.data, character)
        if money is None:
            self.money_balance.configure(text="Unavailable in this save")
            self.money_status.configure(text="This save’s wallet record was not identified; other supported character fields may still be editable.")
            self.money_entry.delete(0, "end")
        else:
            self.money_balance.configure(text=f"{money:,} €$")
            self.money_entry.delete(0, "end")
            self.money_entry.insert(0, str(money))
            self.money_status.configure(text="Wallet and character edits export to a separate save copy with a restore backup.")
        self.money_export_button.configure(state="normal" if character or money is not None else "disabled")

    def _render_save(self, entry: SaveEntry | None):
        if entry is None:
            self.character_values = {}
            self.money_value = None
            for key in self.metric_values:
                self.metric_values[key].configure(text="—")
            self.profile_name.configure(text="No save selected")
            self.profile_info.configure(text="Choose the save folder containing your Cyberpunk 2077 slots.")
            self.cover.configure(image="", text="NO SAVE SLOT FOUND", compound="center")
            self.photo = None
            self.copy_button.configure(state="disabled")
            self.backup_save_button.configure(state="disabled")
            self.restore_save_button.configure(state="disabled")
            self.money_balance.configure(text="No save selected")
            self.money_status.configure(text="Select a save slot to inspect its saved balance.")
            self.money_entry.delete(0, "end")
            self.money_export_button.configure(state="disabled")
            self._render_profile_fields({})
            self._render_technical_fields(None)
            self.parser_status.configure(text="Choose a save folder to scan for sav.dat files.")
            return
        metadata = entry.data
        self.title_label.configure(text=entry.title)
        self.subtitle.configure(text="Save slot overview  ·  source files remain unchanged")
        self.profile_name.configure(text=f"Level {self._format_number(metadata.get('level'), '—')} V")
        saved = datetime.fromtimestamp(entry.modified).strftime("%b %d, %Y  ·  %H:%M") if entry.modified else "Unknown"
        self.profile_info.configure(text=(f"{metadata.get('lifePath', 'Unknown life path')}  ·  {metadata.get('bodyGender', 'Unknown')}\n"
                                           f"Difficulty: {metadata.get('difficulty', 'Unknown')}\n"
                                           f"Last saved: {saved}"))
        hours = metadata.get("playTime")
        self.metric_values["level"].configure(text=self._format_number(metadata.get("level")))
        self.metric_values["streetCred"].configure(text=self._format_number(metadata.get("streetCred")))
        self.metric_values["playTime"].configure(text=f"{float(hours)/3600:.1f} h" if isinstance(hours, (int, float)) else "—")
        self.metric_values["lifePath"].configure(text=metadata.get("lifePath", "Unknown"))
        self._render_profile_fields(metadata)
        self._render_technical_fields(entry)
        self._load_cover(entry)

    def _load_cover(self, entry: SaveEntry):
        shot = entry.folder / "screenshot.png"
        try:
            photo = tk.PhotoImage(file=str(shot))
            scale = max(1, (photo.width() + 780 - 1) // 780, (photo.height() + 560 - 1) // 560)
            if scale > 1:
                photo = photo.subsample(scale, scale)
            self.photo = photo
            self.cover.configure(image=photo, text="", compound="none")
        except (tk.TclError, OSError):
            self.photo = None
            self.cover.configure(image="", text="SCREENSHOT NOT AVAILABLE", compound="center")

    def _fit_cover(self, _event=None):
        # The screenshot is loaded at a conservative fixed maximum to avoid resize churn.
        return

    def _render_profile_fields(self, metadata: dict, snapshot=None):
        for child in self.profile_fields.winfo_children():
            if child is not self.wallet_panel:
                child.destroy()
        self.character_entries = {}
        values = self.character_values
        self.profile_fields.grid_columnconfigure(1, weight=1)
        self.profile_fields.grid_columnconfigure(3, weight=1)

        def add_group(title, fields, col, row):
            tk.Label(self.profile_fields, text=title, background=COLORS["surface"],
                     foreground=COLORS["cyan"], font=("Segoe UI Semibold", 10)).grid(
                         row=row, column=col, columnspan=2, sticky="w", pady=(0, 7))
            for index, (key, label) in enumerate(fields, start=row + 1):
                tk.Label(self.profile_fields, text=label, background=COLORS["surface"],
                         foreground=COLORS["muted"], font=("Segoe UI Semibold", 10), anchor="w").grid(
                             row=index, column=col, sticky="w", padx=(0, 10), pady=4)
                value = values.get(key)
                if value is None:
                    text = str(metadata.get(key, "—"))
                    field = ttk.Entry(self.profile_fields, width=12)
                    field.insert(0, text)
                    field.configure(state="disabled")
                else:
                    field = self._limited_entry(self.profile_fields, EDIT_LIMITS[key], width=12)
                    field.configure(validate="none")
                    field.insert(0, str(value))
                    field.configure(validate="key", validatecommand=field._limit_validatecommand)
                    self.character_entries[key] = field
                field.grid(row=index, column=col + 1, sticky="ew", padx=(0, 20), pady=4)

        add_group("ATTRIBUTES", [
            ("strength", "Body"), ("intelligence", "Intelligence"), ("reflexes", "Reflexes"),
            ("technicalAbility", "Technical Ability"), ("cool", "Cool"),
        ], 0, 0)
        add_group("SKILL PROGRESSIONS", [
            ("headHunter", "Headhunter"), ("netrunner", "Netrunner"), ("shinobi", "Shinobi"),
            ("solo", "Solo"), ("engineeringProgression", "Engineering"),
        ], 2, 0)
        add_group("PROGRESSION & POINTS", [
            ("level", "Level"), ("streetCred", "Street Cred"),
            ("attributePoints", "Attribute Points"), ("perkPoints", "Perk Points"),
        ], 0, 7)
        if not values:
            tk.Label(self.profile_fields, text="Character values are unavailable for this save.",
                     background=COLORS["surface"], foreground=COLORS["muted"],
                     font=("Segoe UI", 10)).grid(row=1, column=0, columnspan=4, sticky="w")

    def _render_technical_fields(self, entry: SaveEntry | None):
        for item in self.technical_tree.get_children():
            self.technical_tree.delete(item)
        if not entry:
            self.technical_tree.insert("", "end", text="Save slot", values=("Select a slot to inspect its metadata.",))
            return

        data = entry.data

        def play_time(value):
            if not isinstance(value, (int, float)):
                return "—"
            total_minutes = max(0, int(value // 60))
            return f"{total_minutes // 60} h {total_minutes % 60:02d} min"

        groups = [
            ("Character", [
                ("Life path", data.get("lifePath", "—")),
                ("Body / brain gender", f"{data.get('bodyGender', '—')} / {data.get('brainGender', '—')}"),
                ("Character level", data.get("level", "—")),
                ("Street Cred", data.get("streetCred", "—")),
                ("Difficulty", data.get("difficulty", "—")),
            ]),
            ("Attributes", [(label, data.get(key, "—")) for key, label in (
                ("strength", "Body"), ("intelligence", "Intelligence"), ("reflexes", "Reflexes"),
                ("technicalAbility", "Technical Ability"), ("cool", "Cool"),
            )]),
            ("Skill progression", [(label, data.get(key, "—")) for key, label in (
                ("gunslinger", "Gunslinger"), ("assault", "Assault"), ("demolition", "Demolition"),
                ("athletics", "Athletics"), ("brawling", "Brawling"), ("coldBlood", "Cold Blood"),
                ("stealth", "Stealth"), ("engineering", "Engineering"), ("crafting", "Crafting"),
                ("hacking", "Hacking"), ("combatHacking", "Combat Hacking"),
            )]),
            ("Progress & location", [
                ("Play time", play_time(data.get("playTime"))),
                ("Playthrough time", play_time(data.get("playthroughTime"))),
                ("Location", data.get("locationName", "—")),
                ("Tracked quest", data.get("trackedQuest", "—")),
                ("Main quest", data.get("mainQuest", "—")),
                ("Save slot name", data.get("name", entry.folder.name)),
                ("Modded save flag", data.get("isModded", "—")),
                ("Checkpoint save", data.get("isCheckpoint", "—")),
                ("End-game save", data.get("isEndGameSave", "—")),
                ("Point-of-no-return save", data.get("isPointOfNoReturn", "—")),
            ]),
            ("Version & files", [
                ("Build patch", data.get("buildPatch", "—")),
                ("Game version", data.get("gameVersion", "—")),
                ("Save version", data.get("saveVersion", "—")),
                ("Metadata timestamp", data.get("timestampString", "—")),
                ("Platform", data.get("platform", "—")),
                ("Save data size", f"{(entry.folder / 'sav.dat').stat().st_size:,} bytes"),
                ("Screenshot", "Present" if (entry.folder / "screenshot.png").is_file() else "Missing"),
                ("Save folder", str(entry.folder)),
            ]),
        ]
        for group_name, values in groups:
            group_id = self.technical_tree.insert("", "end", text=group_name, values=(f"{len(values)} fields",), open=group_name in ("Character", "Version & files"), tags=("group",))
            for label, value in values:
                self.technical_tree.insert(group_id, "end", text=label, values=(str(value),))

    def create_safety_copy(self):
        if not self.selected:
            return
        destination_root = filedialog.askdirectory(title="Choose where to create the safety copy",
                                                   initialdir=str(self.selected.folder.parent))
        if not destination_root:
            return
        source = self.selected.folder
        base = Path(destination_root) / f"{source.name}-safety-copy"
        destination = base
        number = 2
        while destination.exists():
            destination = base.with_name(f"{base.name}-{number}")
            number += 1
        try:
            shutil.copytree(source, destination)
        except OSError as exc:
            self._message("Error", "Safety copy failed", str(exc))
            return
        self.status.configure(text=f"Safety copy created · {destination}")

    @staticmethod
    def _save_backup_path(entry: SaveEntry) -> Path:
        return entry.folder / "sav.dat.ncsbak"

    def backup_selected_save(self):
        """Create a persistent, validated copy beside the selected save data."""
        if not self.selected:
            return
        source = self.selected.folder / "sav.dat"
        backup = self._save_backup_path(self.selected)
        if not source.is_file():
            self._message("Error", "Backup unavailable", "The selected save has no sav.dat file.")
            return
        if backup.exists() and not self._message("Confirmation", "Replace existing backup?",
                f"A backup already exists:\n{backup}\n\nReplace it with the current sav.dat?"):
            return
        temporary = backup.with_suffix(backup.suffix + ".tmp")
        try:
            shutil.copy2(source, temporary)
            read_save_header(temporary)
            os.replace(temporary, backup)
        except (OSError, ValueError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            self._message("Error", "Backup failed", str(exc))
            return
        self.restore_save_button.configure(state="normal")
        self.status.configure(text=f"sav.dat backup created · {backup.name}")

    def restore_selected_save(self):
        """Restore the selected slot's sav.dat from its side-by-side backup."""
        if not self.selected:
            return
        target = self.selected.folder / "sav.dat"
        backup = self._save_backup_path(self.selected)
        if not backup.is_file():
            self._message("Information", "No backup found", "Create a sav.dat backup before restoring.")
            self.restore_save_button.configure(state="disabled")
            return
        try:
            read_save_header(backup)
        except (OSError, ValueError) as exc:
            self._message("Error", "Backup is invalid", str(exc))
            return
        if not self._message("Confirmation", "Restore original save data?",
                "This replaces this slot's sav.dat with the saved backup.\n\n"
                "The current sav.dat will first be preserved as a timestamped recovery copy.\n\nContinue?"):
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        emergency = self.selected.folder / f"sav.dat.before-restore-{stamp}"
        temporary = target.with_suffix(".dat.restore-tmp")
        try:
            if target.is_file():
                shutil.copy2(target, emergency)
            shutil.copy2(backup, temporary)
            read_save_header(temporary)
            os.replace(temporary, target)
        except (OSError, ValueError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            self._message("Error", "Restore failed", str(exc))
            return
        self.status.configure(text=f"Restored from backup · recovery copy: {emergency.name}")
        self.parser_status.configure(text=f"Restored from backup\n{target}")

    def export_character_copy(self):
        """Export wallet/character edits into a new slot folder, preserving its source."""
        if not self.selected:
            return
        fields: dict[str, int] = {}
        try:
            for key, entry in self.character_entries.items():
                text = entry.get().strip().replace(",", "")
                if not text.isdecimal():
                    raise ValueError(f"{key} must be a whole number.")
                value = int(text)
                maximum = EDIT_LIMITS[key]
                original_value = self.character_values.get(key)
                # Keep legacy over-cap values unchanged; reject only invalid edits.
                if value > maximum and value != original_value:
                    raise ValueError(f"{key} must be between 0 and {maximum}.")
                if value != original_value:
                    fields[key] = value
        except ValueError as exc:
            self._message("Error", "Invalid character value", str(exc))
            return

        amount = None
        if self.money_value is not None:
            try:
                amount_text = self.money_entry.get().strip().replace(",", "")
                if not amount_text.isdecimal():
                    raise ValueError(f"Wallet balance must be a whole number from 0 to {EDIT_LIMITS['money']:,}.")
                amount = int(amount_text)
                if amount > EDIT_LIMITS["money"]:
                    raise ValueError(f"Wallet balance exceeds the supported maximum of {EDIT_LIMITS['money']:,}.")
                if amount != self.money_value:
                    fields["money"] = amount
            except ValueError as exc:
                self._message("Error", "Invalid wallet value", str(exc))
                return
        if not fields:
            self._message("Information", "No edits to export", "Change one or more supported values first.")
            return

        selected = self.selected
        destination_root = filedialog.askdirectory(
            title="Choose the folder for the edited save copy",
            initialdir=str(selected.folder.parent))
        if not destination_root:
            return
        base = Path(destination_root) / f"{selected.folder.name}-Edited"
        destination = base
        suffix = 2
        while destination.exists():
            destination = base.with_name(f"{base.name}-{suffix}")
            suffix += 1
        try:
            shutil.copytree(selected.folder, destination)
            original_copy = destination / "sav.dat"
            backup = destination / "sav.dat.ncsbak"
            shutil.copy2(original_copy, backup)
            temporary = destination / "sav.dat.edited-tmp"
            SaveContainer(selected.folder / "sav.dat").save_fields_copy(temporary, fields)
            os.replace(temporary, original_copy)
            metadata_path = destination / "metadata.9.json"
            if metadata_path.is_file():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
                data = metadata.get("Data", {}).get("metadata", {})
                if isinstance(data, dict):
                    data["name"] = destination.name
                    data["fileSize"] = original_copy.stat().st_size
                    for key in ("level", "streetCred", "strength", "intelligence", "reflexes", "technicalAbility", "cool",
                                "athletics", "brawling", "coldBlood", "combatHacking", "coolSkill", "crafting",
                                "demolition", "engineering", "espionage", "gunslinger", "hacking",
                                "intelligenceSkill", "kenjutsu", "reflexesSkill", "stealth", "strengthSkill",
                                "technicalAbilitySkill"):
                        if key in fields:
                            data[key] = fields[key]
                    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self._message("Error", "Export failed", f"The source save was not changed.\n\n{exc}")
            return
        self.status.configure(text=f"Edited save copy exported · {destination}")
        summary = "\n".join(f"{key}: {value:,}" for key, value in fields.items())
        self._message("Information", "Edited copy ready",
            f"Changes:\n{summary}\n\nSaved as a separate folder:\n{destination}\n\nThe original save was not changed. A sav.dat.ncsbak was included for restore.")

    @staticmethod
    def _format_number(value, fallback="—"):
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (int, float)):
            return f"{value:g}"
        return fallback


if __name__ == "__main__":
    app = CyberpunkSaveEditor()
    splash = StartupSplash(app, lambda: None)
    app.show_centered()
    splash.start()
    app.mainloop()
