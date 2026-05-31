import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "EDR Project Sharer"
APP_VERSION = "0.5.13"
SYSTEM = platform.system()

COLORS = {
    "header_bg": "#1d4ed8",
    "header_sub": "#bfdbfe",
    "body_bg": "#eef2f7",
    "card_bg": "#ffffff",
    "text": "#0f172a",
    "muted": "#64748b",
    "border": "#e2e8f0",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
}

TICK_SECONDS = 0.04

# Minimum time per stage so the bar does not zip to 100% on fast disks.
# Work still runs for real; display progress uses max(bytes done, time elapsed).
MIN_STAGE_SECONDS = {
    "prepare": 2.5,
    "copy": 6.5,
    "path": 2.0,
    "finish": 1.0,
}


def default_install_dir():
    home = Path.home()
    if SYSTEM == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", home))
        return base / "EDR"
    if SYSTEM == "Darwin":
        return home / "Applications" / "EDR"
    return home / ".local" / "share" / "edr"


def resource_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def current_user_path():
    if SYSTEM != "Windows":
        return os.environ.get("PATH", "")

    try:
        result = subprocess.run(
            ["reg", "query", r"HKCU\Environment", "/v", "Path"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in result.stdout.splitlines():
            if "Path" in line and "REG_" in line:
                return line.split("REG_", 1)[1].split(None, 1)[1]
    except Exception:
        pass
    return os.environ.get("PATH", "")


def _append_unique_line(path, line):
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="ignore")
    if line in existing:
        return
    with path.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(line + "\n")


def _is_edr_path_entry(entry):
    if not entry:
        return False
    upper = entry.upper()
    return (
        "\\EDR\\" in upper
        or upper.endswith("\\EDR")
        or "\\EDR-SETUP\\" in upper
        or "\\NODE_MODULES\\@ENDERAIR\\EDR" in upper
    )


def remove_npm_legacy_edr():
    if SYSTEM != "Windows":
        return
    try:
        subprocess.run(
            ["npm", "uninstall", "-g", "@enderair/edr", "--loglevel=error"],
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        pass


def iter_old_install_dirs(target_dir: Path):
    home = Path.home()
    candidates = {target_dir}
    if SYSTEM == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home))
        roaming = Path(os.environ.get("APPDATA", home))
        candidates.update({
            local / "EDR",
            local / "EDR" / "EDR-Setup",
            roaming / "EDR",
        })
    elif SYSTEM == "Darwin":
        candidates.update({
            home / "Applications" / "EDR",
            home / ".local" / "share" / "edr",
        })
    else:
        candidates.update({
            home / ".local" / "share" / "edr",
        })

    for candidate in sorted(candidates, key=lambda item: str(item).lower()):
        yield candidate


def remove_old_edr_system(target_dir: Path):
    removed = 0
    for old_dir in iter_old_install_dirs(target_dir):
        if old_dir.exists():
            shutil.rmtree(old_dir, ignore_errors=True)
            removed += 1
    return removed


def add_to_user_path(folder):
    folder_text = str(folder)
    if SYSTEM == "Windows":
        existing = current_user_path()
        parts = [
            part.strip()
            for part in existing.split(";")
            if part.strip() and not _is_edr_path_entry(part)
        ]
        parts = [part for part in parts if part.lower() != folder_text.lower()]
        updated = ";".join([folder_text, *parts])
        subprocess.run(
            ["setx", "Path", updated],
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return

    shell = Path.home() / (".zprofile" if SYSTEM == "Darwin" else ".profile")
    line = f'export PATH="{folder_text}:$PATH"'
    _append_unique_line(shell, line)


def iter_copy_plan(source: Path):
    for item in source.rglob("*"):
        if item.is_file():
            yield item, item.relative_to(source)


class SetupApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} Setup")
        self.geometry("600x520")
        self.resizable(False, False)
        self.configure(bg=COLORS["body_bg"])

        icon_path = resource_path("icon.ico")
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                pass

        self._configure_styles()

        self.install_dir = tk.StringVar(value=str(default_install_dir()))
        self.add_path = tk.BooleanVar(value=True)
        self.status_text = tk.StringVar(value="Ready to install")
        self.progress_value = tk.DoubleVar(value=0.0)
        self.percent_text = tk.StringVar(value="0%")

        self._build_header()
        self._build_body()
        self._build_footer()

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista" if SYSTEM == "Windows" else "clam")
        except tk.TclError:
            style.theme_use("clam")

        style.configure(
            "Install.Horizontal.TProgressbar",
            troughcolor=COLORS["border"],
            background=COLORS["accent"],
            thickness=10,
            borderwidth=0,
            lightcolor=COLORS["accent"],
            darkcolor=COLORS["accent"],
        )
        style.configure(
            "Accent.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(18, 8),
        )
        style.configure(
            "Ghost.TButton",
            font=("Segoe UI", 10),
            padding=(16, 8),
        )
        style.configure(
            "Card.TCheckbutton",
            font=("Segoe UI", 10),
            background=COLORS["card_bg"],
            foreground=COLORS["text"],
        )
        style.map("Accent.TButton", background=[("active", COLORS["accent_hover"])])

    def _build_header(self):
        header = tk.Frame(self, bg=COLORS["header_bg"], height=118)
        header.pack(fill="x")
        header.pack_propagate(False)

        inner = tk.Frame(header, bg=COLORS["header_bg"])
        inner.pack(fill="both", expand=True, padx=28, pady=22)

        tk.Label(
            inner,
            text="EDR Setup",
            font=("Segoe UI", 22, "bold"),
            fg="white",
            bg=COLORS["header_bg"],
        ).pack(anchor="w")

        tk.Label(
            inner,
            text=APP_NAME,
            font=("Segoe UI", 11),
            fg=COLORS["header_sub"],
            bg=COLORS["header_bg"],
        ).pack(anchor="w", pady=(4, 0))

        tk.Label(
            inner,
            text=f"Version {APP_VERSION}",
            font=("Segoe UI", 9),
            fg=COLORS["header_sub"],
            bg=COLORS["header_bg"],
        ).pack(anchor="w", pady=(8, 0))

    def _build_body(self):
        outer = tk.Frame(self, bg=COLORS["body_bg"])
        outer.pack(fill="both", expand=True, padx=24, pady=20)

        card = tk.Frame(
            outer,
            bg=COLORS["card_bg"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        card.pack(fill="both", expand=True)

        content = tk.Frame(card, bg=COLORS["card_bg"])
        content.pack(fill="both", expand=True, padx=22, pady=22)

        detail = "Install the EDR command-line tool on this device."
        if SYSTEM == "Darwin":
            detail = "Install EDR and use it from new Terminal windows."
        elif SYSTEM == "Linux":
            detail = "Install EDR and use it from new shell sessions."

        tk.Label(
            content,
            text=detail,
            font=("Segoe UI", 10),
            fg=COLORS["muted"],
            bg=COLORS["card_bg"],
            wraplength=500,
            justify="left",
        ).pack(anchor="w", pady=(0, 18))

        tk.Label(
            content,
            text="Install location",
            font=("Segoe UI", 10, "bold"),
            fg=COLORS["text"],
            bg=COLORS["card_bg"],
        ).pack(anchor="w")

        row = tk.Frame(content, bg=COLORS["card_bg"])
        row.pack(fill="x", pady=(8, 16))

        self.path_entry = ttk.Entry(row, textvariable=self.install_dir, font=("Segoe UI", 10))
        self.path_entry.pack(side="left", fill="x", expand=True, ipady=4)

        ttk.Button(row, text="Browse…", style="Ghost.TButton", command=self.browse).pack(
            side="left", padx=(10, 0)
        )

        path_label = "Add EDR to my PATH (recommended)"
        if SYSTEM == "Windows":
            path_label = "Add EDR to my user PATH (recommended)"

        ttk.Checkbutton(
            content,
            text=path_label,
            variable=self.add_path,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(0, 20))

        tk.Frame(content, bg=COLORS["border"], height=1).pack(fill="x", pady=(0, 16))

        progress_header = tk.Frame(content, bg=COLORS["card_bg"])
        progress_header.pack(fill="x")

        tk.Label(
            progress_header,
            text="Installation progress",
            font=("Segoe UI", 10, "bold"),
            fg=COLORS["text"],
            bg=COLORS["card_bg"],
        ).pack(side="left")

        tk.Label(
            progress_header,
            textvariable=self.percent_text,
            font=("Segoe UI", 10, "bold"),
            fg=COLORS["accent"],
            bg=COLORS["card_bg"],
        ).pack(side="right")

        self.progress = ttk.Progressbar(
            content,
            variable=self.progress_value,
            maximum=100,
            style="Install.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", pady=(10, 8))

        tk.Label(
            content,
            textvariable=self.status_text,
            font=("Segoe UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["card_bg"],
            wraplength=500,
            justify="left",
        ).pack(anchor="w")

        tk.Label(
            content,
            text="Files are copied to the folder above. Open a new terminal after setup.",
            font=("Segoe UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["card_bg"],
            wraplength=500,
            justify="left",
        ).pack(anchor="w", pady=(14, 0))

    def _build_footer(self):
        footer = tk.Frame(self, bg=COLORS["body_bg"])
        footer.pack(fill="x", padx=24, pady=(0, 22))

        self.cancel_button = ttk.Button(footer, text="Cancel", style="Ghost.TButton", command=self.destroy)
        self.cancel_button.pack(side="right")

        self.install_button = ttk.Button(
            footer, text="Install EDR", style="Accent.TButton", command=self.install
        )
        self.install_button.pack(side="right", padx=(0, 10))

    def browse(self):
        selected = filedialog.askdirectory(initialdir=self.install_dir.get())
        if selected:
            self.install_dir.set(selected)

    def set_progress(self, percent, message):
        percent = max(0, min(100, percent))
        self.progress_value.set(percent)
        self.percent_text.set(f"{int(percent)}%")
        self.status_text.set(message)
        self.update_idletasks()

    def _ui_tick(self):
        self.update_idletasks()
        time.sleep(TICK_SECONDS)

    def _wait_stage_minimum(self, started_at, min_seconds, start_pct, end_pct, message):
        """Keep the bar moving until min_seconds even if work already finished."""
        while True:
            elapsed = time.perf_counter() - started_at
            ratio = min(1.0, elapsed / min_seconds)
            percent = start_pct + (end_pct - start_pct) * ratio
            self.set_progress(percent, message)
            self._ui_tick()
            if elapsed >= min_seconds:
                break

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.install_button.config(state=state)
        self.cancel_button.config(state=state)
        self.path_entry.config(state=state)

    def install(self):
        source = resource_path("edr")
        if not source.exists():
            messagebox.showerror("Setup Error", f"Missing bundled file: {source}")
            return

        target_dir = Path(self.install_dir.get()).expanduser()
        self._set_busy(True)

        try:
            prepare_started = time.perf_counter()
            self.set_progress(2, "Checking for old EDR installs...")
            remove_npm_legacy_edr()
            self.set_progress(5, "Removing old EDR to make space for the new one...")
            removed_old = remove_old_edr_system(target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            cleanup_message = "Old EDR removed. Preparing installation..." if removed_old else "No old EDR install found. Preparing installation..."
            self._wait_stage_minimum(
                prepare_started,
                MIN_STAGE_SECONDS["prepare"],
                5,
                10,
                cleanup_message,
            )

            plan = list(iter_copy_plan(source))
            total_bytes = sum(item.stat().st_size for item, _ in plan) or 1
            copied_bytes = 0
            copy_started = time.perf_counter()
            copy_min = MIN_STAGE_SECONDS["copy"]

            for src_file, rel_path in plan:
                dest_file = target_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                copied_bytes += src_file.stat().st_size

                elapsed = time.perf_counter() - copy_started
                byte_ratio = copied_bytes / total_bytes
                time_ratio = min(1.0, elapsed / copy_min)
                ratio = max(byte_ratio, time_ratio)
                percent = 10 + int(75 * ratio)
                self.set_progress(percent, f"Copying {rel_path.name}…")
                self._ui_tick()

            self._wait_stage_minimum(
                copy_started,
                copy_min,
                max(10, self.progress_value.get()),
                85,
                "Copying EDR files…",
            )

            path_started = time.perf_counter()
            self.set_progress(86, "Updating PATH (EDR first)…")
            if self.add_path.get():
                add_to_user_path(target_dir)
            self._wait_stage_minimum(
                path_started,
                MIN_STAGE_SECONDS["path"],
                86,
                95,
                "Updating PATH…",
            )

            finish_started = time.perf_counter()
            self._wait_stage_minimum(
                finish_started,
                MIN_STAGE_SECONDS["finish"],
                95,
                100,
                "Finishing installation…",
            )
        except Exception as err:
            messagebox.showerror("Setup Error", str(err))
            self._set_busy(False)
            self.set_progress(0, "Installation failed.")
            return

        terminal_hint = "Open a new terminal and run:\n\nedr version"
        if SYSTEM == "Darwin":
            terminal_hint = "Open a new Terminal window and run:\n\nedr version"
        elif SYSTEM == "Linux":
            terminal_hint = "Open a new shell and run:\n\nedr version"

        messagebox.showinfo("Setup Complete", f"EDR was installed successfully.\n\n{terminal_hint}")
        self.destroy()


if __name__ == "__main__":
    SetupApp().mainloop()
