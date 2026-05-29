import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "EDR Project Sharer"
SYSTEM = platform.system()


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


def add_to_user_path(folder):
    folder_text = str(folder)
    if SYSTEM == "Windows":
        existing = current_user_path()
        parts = [part.strip() for part in existing.split(";") if part.strip()]
        if any(part.lower() == folder_text.lower() for part in parts):
            return

        updated = existing.rstrip(";")
        updated = f"{updated};{folder_text}" if updated else folder_text
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
        self.geometry("560x380")
        self.resizable(False, False)

        self.install_dir = tk.StringVar(value=str(default_install_dir()))
        self.add_path = tk.BooleanVar(value=True)
        self.status_text = tk.StringVar(value="Ready to install.")
        self.progress_value = tk.DoubleVar(value=0.0)

        frame = tk.Frame(self, padx=24, pady=22)
        frame.pack(fill="both", expand=True)

        title = f"{APP_NAME} Setup"
        detail = "Install the EDR command line tool on this device."
        if SYSTEM == "Darwin":
            detail = "Install EDR to a local folder and make it available in new Terminal sessions."
        elif SYSTEM == "Linux":
            detail = "Install EDR to a local folder and make it available in new shells."

        tk.Label(frame, text=title, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(frame, text=detail, font=("Segoe UI", 10), wraplength=500, justify="left").pack(anchor="w", pady=(6, 18))

        tk.Label(frame, text="Install folder", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        row = tk.Frame(frame)
        row.pack(fill="x", pady=(5, 12))
        tk.Entry(row, textvariable=self.install_dir).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse", command=self.browse).pack(side="left", padx=(8, 0))

        path_label = "Add EDR to my PATH so I can type edr in a new terminal"
        if SYSTEM == "Windows":
            path_label = "Add EDR to my user PATH so I can type edr in a new terminal"

        tk.Checkbutton(frame, text=path_label, variable=self.add_path).pack(anchor="w", pady=(0, 14))

        self.progress = ttk.Progressbar(frame, variable=self.progress_value, maximum=100)
        self.progress.pack(fill="x", pady=(0, 6))
        tk.Label(frame, textvariable=self.status_text, font=("Segoe UI", 9), fg="#333", wraplength=500, justify="left").pack(
            anchor="w", pady=(0, 12)
        )

        note = "The installer copies the bundled EDR files into the folder you choose."
        if SYSTEM != "Windows":
            note = "The installer copies the bundled EDR files and writes a shell PATH entry when enabled."
        tk.Label(frame, text=note, font=("Segoe UI", 9), fg="#444", wraplength=500, justify="left").pack(anchor="w", pady=(0, 12))

        buttons = tk.Frame(frame)
        buttons.pack(fill="x", side="bottom")
        self.cancel_button = tk.Button(buttons, text="Cancel", command=self.destroy)
        self.cancel_button.pack(side="right")
        self.install_button = tk.Button(buttons, text="Install", width=12, command=self.install)
        self.install_button.pack(side="right", padx=(0, 8))

    def browse(self):
        selected = filedialog.askdirectory(initialdir=self.install_dir.get())
        if selected:
            self.install_dir.set(selected)

    def set_progress(self, percent, message):
        self.progress_value.set(percent)
        self.status_text.set(message)
        self.update_idletasks()

    def install(self):
        source = resource_path("edr")
        if not source.exists():
            messagebox.showerror("Setup Error", f"Missing bundled file: {source}")
            return

        target_dir = Path(self.install_dir.get()).expanduser()
        self.install_button.config(state="disabled")
        self.cancel_button.config(state="disabled")

        try:
            plan = list(iter_copy_plan(source))
            total_bytes = sum(item.stat().st_size for item, _ in plan) or 1
            copied_bytes = 0

            self.set_progress(0, "Preparing install folder....")
            target_dir.mkdir(parents=True, exist_ok=True)

            self.set_progress(5, "Copying EDR files....")
            for index, (src_file, rel_path) in enumerate(plan, start=1):
                dest_file = target_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                copied_bytes += src_file.stat().st_size
                percent = 5 + int(copied_bytes * 75 / total_bytes)
                self.set_progress(percent, f"Copying files.... {percent}")

            self.set_progress(85, "Updating PATH....")
            if self.add_path.get():
                add_to_user_path(target_dir)

            self.set_progress(100, "EDR installed successfully.")
        except Exception as err:
            messagebox.showerror("Setup Error", str(err))
            self.install_button.config(state="normal")
            self.cancel_button.config(state="normal")
            self.set_progress(0, "Install failed.")
            return

        terminal_hint = "Open a new terminal and run:\n\nedr version"
        if SYSTEM == "Darwin":
            terminal_hint = "Open a new Terminal window and run:\n\nedr version"
        elif SYSTEM == "Linux":
            terminal_hint = "Open a new shell and run:\n\nedr version"

        messagebox.showinfo("Setup Complete", f"EDR was installed.\n\n{terminal_hint}")
        self.destroy()


if __name__ == "__main__":
    SetupApp().mainloop()
