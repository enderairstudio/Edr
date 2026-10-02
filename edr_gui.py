"""Small desktop shell for EDR. It delegates all transfers to command.py."""
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent


class EdrDesktop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EDR — Project Sharer")
        self.minsize(760, 540)
        self.columnconfigure(0, weight=1); self.rowconfigure(3, weight=1)
        self.events = queue.Queue()
        self.folder = tk.StringVar(value=str(Path.cwd()))
        self.remote = tk.StringVar(); self.destination = tk.StringVar()
        self.relay_url = tk.StringVar(value="http://127.0.0.1:8765")
        self.fast = tk.BooleanVar(value=True); self.relay = tk.BooleanVar()
        self._build(); self.after(100, self._drain_events); self.refresh_profiles()

    def _build(self):
        body = ttk.Frame(self, padding=16); body.grid(sticky="nsew"); body.columnconfigure(1, weight=1); body.rowconfigure(5, weight=1)
        ttk.Label(body, text="EDR Project Sharer", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="Share a folder over your LAN or through a relay code.").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 12))
        self._path_row(body, 2, "Folder", self.folder, self.pick_folder)
        ttk.Checkbutton(body, text="Use relay (share anywhere)", variable=self.relay).grid(row=3, column=0, sticky="w", pady=4)
        ttk.Checkbutton(body, text="Fast mode (skip ZIP compression)", variable=self.fast).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Button(body, text="Share now", command=self.share).grid(row=3, column=2, sticky="e")
        ttk.Separator(body).grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Label(body, text="Receive from IP or Edrnko_ code").grid(row=5, column=0, sticky="w")
        ttk.Entry(body, textvariable=self.remote).grid(row=5, column=1, sticky="ew", padx=8)
        ttk.Button(body, text="Pull", command=self.pull).grid(row=5, column=2, sticky="e")
        self._path_row(body, 6, "Save into", self.destination, self.pick_destination, optional=True)
        ttk.Label(body, text="Relay URL").grid(row=7, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.relay_url).grid(row=7, column=1, sticky="ew", padx=8)
        ttk.Button(body, text="Start relay", command=self.start_relay).grid(row=7, column=2, sticky="e")
        ttk.Label(body, text="Saved profiles").grid(row=8, column=0, sticky="w", pady=(12, 2))
        self.profiles = tk.Listbox(body, height=5); self.profiles.grid(row=9, column=0, columnspan=2, sticky="nsew")
        ttk.Button(body, text="Refresh", command=self.refresh_profiles).grid(row=9, column=2, sticky="ne")
        ttk.Label(body, text="Activity").grid(row=10, column=0, sticky="w", pady=(12, 2))
        self.log = tk.Text(body, height=9, state="disabled", wrap="word"); self.log.grid(row=11, column=0, columnspan=3, sticky="nsew")

    def _path_row(self, parent, row, label, variable, command, optional=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2, sticky="e")

    def pick_folder(self):
        if path := filedialog.askdirectory(initialdir=self.folder.get() or str(Path.cwd())): self.folder.set(path)
    def pick_destination(self):
        if path := filedialog.askdirectory(initialdir=self.destination.get() or str(Path.cwd())): self.destination.set(path)
    def _append(self, text):
        self.log.configure(state="normal"); self.log.insert("end", text); self.log.see("end"); self.log.configure(state="disabled")
    def run(self, args):
        self._append("\n$ edr " + " ".join(args) + "\n")
        def worker():
            process = subprocess.Popen([sys.executable, str(ROOT / "command.py"), *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            for line in process.stdout: self.events.put(line)
            self.events.put(f"[process finished: {process.wait()}]\n")
        threading.Thread(target=worker, daemon=True).start()
    def _drain_events(self):
        while not self.events.empty(): self._append(self.events.get())
        self.after(100, self._drain_events)
    def share(self):
        folder = self.folder.get().strip()
        if not Path(folder).is_dir(): return messagebox.showerror("EDR", "Choose a valid folder first.")
        args = ["share", folder, "--fast"] if self.fast.get() else ["share", folder]
        if self.relay.get(): args += ["--non-network", "--idnew", "--relay-url", self.relay_url.get().strip()]
        self.run(args)
    def pull(self):
        remote = self.remote.get().strip()
        if not remote: return messagebox.showerror("EDR", "Enter a LAN IP or Edrnko_ code.")
        args = ["pull", remote]
        if self.destination.get().strip(): args += ["--to", self.destination.get().strip()]
        if remote.startswith("Edrnko_"): args += ["--relay-url", self.relay_url.get().strip()]
        self.run(args)
    def start_relay(self): self.run(["relay", "start", "--port", "8765"])
    def refresh_profiles(self): self.run(["list"])

if __name__ == "__main__": EdrDesktop().mainloop()
