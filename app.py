"""MultiPinger: lightweight desktop ICMP reachability monitor.

Run: python app.py
Requires Python 3.10+, Tkinter, and the operating system's ping utility.
No pip packages are needed.
"""
from __future__ import annotations

import csv
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from collections import deque
from typing import Optional

from ping_core import PingResult, PingStats, ping_once, validate_target


BG = "#f4f7fb"
NAVY = "#12233f"
NAVY_LIGHT = "#263b59"
TEXT = "#18273d"
MUTED = "#67778e"
BORDER = "#dfe6ef"
TEAL = "#0b827b"
GREEN = "#087a52"
RED = "#c63845"
AMBER = "#a56c16"
BLUE = "#2663ba"
WHITE = "#ffffff"

MAX_TARGETS = 64
MAX_RESULTS = 20000  # Exportable observations retained in memory (not on disk).
DEFAULT_TARGETS = ("1.1.1.1", "8.8.8.8")


def config_path() -> Path:
    """Use an OS-appropriate user configuration folder."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "MultiPinger"
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "MultiPinger"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "multipinger"
    return base / "settings.json"


def load_config() -> dict:
    path = config_path()
    try:
        with path.open("r", encoding="utf-8") as file:
            result = json.load(file)
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def pretty_latency(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value < 1:
        return f"{value:.2f} ms"
    return f"{value:.1f} ms"


class MultiPinger(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MultiPinger | Network Troubleshooting")
        self.geometry("1190x820")
        self.minsize(950, 680)
        self.configure(bg=BG)

        self.stats: dict[str, PingStats] = {}
        self.workers: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self.pending: queue.Queue[tuple[int, PingResult]] = queue.Queue()
        self.samples: deque[PingResult] = deque(maxlen=MAX_RESULTS)
        self.running = False
        self.generation = 0
        self.active_interval = 2.0
        self.active_timeout = 1.5
        self.selected_target: Optional[str] = None
        self._sort_col: Optional[str] = None
        self._sort_reverse = False

        saved = load_config()
        self.interval_var = tk.StringVar(value=str(self._in_range(saved.get("interval"), 2, 1, 60)))
        self.timeout_var = tk.StringVar(value=str(self._in_range(saved.get("timeout"), 1.5, 0.5, 10)))
        self.target_var = tk.StringVar()
        self.app_state_var = tk.StringVar(value="●  PAUSED")
        self.hint_var = tk.StringVar(value="Add destinations, then select Start Monitoring.")
        self.chart_title_var = tk.StringVar(value="Latency history")
        self.chart_subtitle_var = tk.StringVar(value="Select a destination to see its last 60 checks.")
        self._card_values: dict[str, tk.Label] = {}

        self._init_styles()
        self._build_layout()

        raw_targets = saved.get("targets", DEFAULT_TARGETS)
        if not isinstance(raw_targets, list) and not isinstance(raw_targets, tuple):
            raw_targets = DEFAULT_TARGETS
        self._add_targets(raw_targets[:MAX_TARGETS], persist=False, notify=False)
        self._refresh_overview()
        self._refresh_buttons()
        self.after(125, self._drain_results)
        self.protocol("WM_DELETE_WINDOW", self._close)

    @staticmethod
    def _in_range(value: object, default: float, low: float, high: float) -> float:
        try:
            number = float(value)
            return number if low <= number <= high else default
        except (TypeError, ValueError):
            return default

    def _init_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("App.TFrame", background=BG)
        style.configure("Panel.TFrame", background=WHITE)
        style.configure("App.TLabel", background=BG, foreground=TEXT, font=("Arial", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Arial", 9))
        style.configure("Panel.TLabel", background=WHITE, foreground=TEXT, font=("Arial", 10))
        style.configure("PanelMuted.TLabel", background=WHITE, foreground=MUTED, font=("Arial", 9))
        style.configure("Accent.TButton", font=("Arial", 10, "bold"),
                        foreground=WHITE, background=TEAL, padding=(15, 9), borderwidth=0)
        style.map("Accent.TButton", background=[("disabled", "#9bb8b4"), ("active", "#096e69")])
        style.configure("Regular.TButton", font=("Arial", 10), foreground=TEXT,
                        background=WHITE, padding=(12, 9), borderwidth=1, relief="solid")
        style.map("Regular.TButton", background=[("active", "#eaf0f7"), ("disabled", "#eef1f5")])
        style.configure("Danger.TButton", font=("Arial", 10), foreground=RED,
                        background=WHITE, padding=(11, 9), borderwidth=1)
        style.map("Danger.TButton", background=[("active", "#fff1f1")])
        style.configure("TEntry", padding=7)
        style.configure("TSpinbox", padding=6)
        style.configure("Monitor.Treeview", background=WHITE, fieldbackground=WHITE,
                        foreground=TEXT, rowheight=31, borderwidth=0, font=("Arial", 10))
        style.configure("Monitor.Treeview.Heading", background="#edf2f8",
                        foreground=NAVY, font=("Arial", 9, "bold"), relief="flat", padding=(7, 10))
        style.map("Monitor.Treeview", background=[("selected", "#d6e7fc")],
                  foreground=[("selected", NAVY)])
        style.map("Monitor.Treeview.Heading", background=[("active", "#e0eaf6")])
        style.configure("Events.Treeview", background=WHITE, fieldbackground=WHITE,
                        foreground=TEXT, rowheight=27, borderwidth=0, font=("Arial", 9))
        style.configure("Events.Treeview.Heading", background="#edf2f8",
                        foreground=NAVY, font=("Arial", 9, "bold"), relief="flat", padding=(4, 7))
        style.map("Events.Treeview", background=[("selected", "#d6e7fc")])

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=5)
        self.rowconfigure(4, weight=3)

        self._build_header()
        self._build_controls()
        self._build_cards()
        self._build_table()
        self._build_bottom_panels()
        ttk.Label(self, textvariable=self.hint_var, style="Muted.TLabel", anchor="w").grid(
            row=5, column=0, sticky="ew", padx=23, pady=(3, 12))

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=NAVY, padx=24, pady=17)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="MultiPinger", bg=NAVY, fg=WHITE,
                 font=("Arial", 21, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="Real-time reachability, packet loss and latency monitoring",
                 bg=NAVY, fg="#b9cbe0", font=("Arial", 10)).grid(row=1, column=0, sticky="w", pady=(4, 0))
        tk.Label(header, textvariable=self.app_state_var, bg=NAVY_LIGHT, fg="#d0e5f5",
                 font=("Arial", 10, "bold"), padx=13, pady=8).grid(row=0, column=1, rowspan=2)

    def _build_controls(self) -> None:
        controls = tk.Frame(self, bg=BG, padx=22, pady=13)
        controls.grid(row=1, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)
        tk.Label(controls, text="Destination", bg=BG, fg=TEXT, font=("Arial", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 9))
        entry = ttk.Entry(controls, textvariable=self.target_var, width=31)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        entry.bind("<Return>", lambda event: self._add_entry())
        ttk.Button(controls, text="+ Add", style="Regular.TButton", command=self._add_entry).grid(
            row=0, column=2, padx=(0, 7))
        ttk.Button(controls, text="Paste list…", style="Regular.TButton", command=self._show_bulk_dialog).grid(
            row=0, column=3, padx=(0, 16))
        tk.Label(controls, text="Interval", bg=BG, fg=TEXT).grid(row=0, column=4, padx=(0, 5))
        self.interval_spin = ttk.Spinbox(controls, from_=1, to=60, increment=1,
                                        width=4, textvariable=self.interval_var)
        self.interval_spin.grid(row=0, column=5, padx=(0, 5))
        tk.Label(controls, text="s", bg=BG, fg=MUTED).grid(row=0, column=6, padx=(0, 10))
        tk.Label(controls, text="Timeout", bg=BG, fg=TEXT).grid(row=0, column=7, padx=(0, 5))
        self.timeout_spin = ttk.Spinbox(controls, from_=0.5, to=10, increment=0.5,
                                       width=4, textvariable=self.timeout_var)
        self.timeout_spin.grid(row=0, column=8, padx=(0, 5))
        tk.Label(controls, text="s", bg=BG, fg=MUTED).grid(row=0, column=9, padx=(0, 11))
        self.start_btn = ttk.Button(controls, text="▶  Start Monitoring",
                                    style="Accent.TButton", command=self._start)
        self.start_btn.grid(row=0, column=10, padx=(0, 6))
        self.stop_btn = ttk.Button(controls, text="■ Stop", style="Regular.TButton", command=self._stop)
        self.stop_btn.grid(row=0, column=11)

    def _build_cards(self) -> None:
        container = tk.Frame(self, bg=BG, padx=22)
        container.grid(row=2, column=0, sticky="ew", pady=(0, 11))
        for index, (key, caption, color) in enumerate((
            ("total", "DESTINATIONS", BLUE),
            ("up", "REACHABLE", GREEN),
            ("down", "UNREACHABLE", RED),
            ("latency", "AVG LATENCY", TEAL),
        )):
            container.columnconfigure(index, weight=1, uniform="cards")
            frame = tk.Frame(container, bg=WHITE, padx=17, pady=12, highlightbackground=BORDER,
                             highlightthickness=1)
            frame.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0))
            tk.Label(frame, text=caption, bg=WHITE, fg=MUTED, font=("Arial", 9, "bold")).pack(anchor="w")
            value = tk.Label(frame, text="—", bg=WHITE, fg=color, font=("Arial", 21, "bold"))
            value.pack(anchor="w", pady=(6, 0))
            self._card_values[key] = value

    def _build_table(self) -> None:
        panel = tk.Frame(self, bg=WHITE, padx=14, pady=12, highlightbackground=BORDER,
                         highlightthickness=1)
        panel.grid(row=3, column=0, sticky="nsew", padx=22, pady=(0, 10))
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)
        toolbar = tk.Frame(panel, bg=WHITE)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(0, weight=1)
        tk.Label(toolbar, text="Live destinations", bg=WHITE, fg=NAVY,
                 font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w")
        self.remove_btn = ttk.Button(toolbar, text="Remove selected", style="Danger.TButton", command=self._remove)
        self.remove_btn.grid(row=0, column=1, padx=(8, 4))
        ttk.Button(toolbar, text="Clear statistics", style="Regular.TButton", command=self._clear_stats).grid(
            row=0, column=2, padx=(4, 4))
        ttk.Button(toolbar, text="Export CSV…", style="Regular.TButton", command=self._export).grid(
            row=0, column=3, padx=(4, 0))

        tree_frame = tk.Frame(panel, bg=WHITE)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        columns = ("target", "state", "last", "avg", "min", "max", "loss", "sent", "checked")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                 style="Monitor.Treeview", selectmode="extended")
        headings = ("DESTINATION", "STATUS", "LATEST", "AVERAGE", "MIN", "MAX", "LOSS", "SENT", "CHECKED")
        widths = (175, 116, 95, 95, 82, 82, 75, 62, 98)
        for col, title, width in zip(columns, headings, widths):
            self.tree.heading(col, text=title, command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=width, minwidth=55, stretch=(col == "target"),
                             anchor="w" if col in ("target", "state") else "center")
        self.tree.tag_configure("up", foreground=GREEN)
        self.tree.tag_configure("down", foreground=RED)
        self.tree.tag_configure("waiting", foreground=MUTED)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)

    def _build_bottom_panels(self) -> None:
        lower = tk.Frame(self, bg=BG, padx=22)
        lower.grid(row=4, column=0, sticky="nsew", pady=(0, 6))
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)
        lower.rowconfigure(0, weight=1)
        graph_panel = tk.Frame(lower, bg=WHITE, padx=13, pady=12,
                               highlightthickness=1, highlightbackground=BORDER)
        graph_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        graph_panel.rowconfigure(2, weight=1)
        graph_panel.columnconfigure(0, weight=1)
        tk.Label(graph_panel, textvariable=self.chart_title_var, bg=WHITE, fg=NAVY,
                 font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(graph_panel, textvariable=self.chart_subtitle_var, bg=WHITE, fg=MUTED,
                 font=("Arial", 9)).grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.graph = tk.Canvas(graph_panel, bg=WHITE, height=145, highlightthickness=0)
        self.graph.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        self.graph.bind("<Configure>", lambda event: self._redraw_graph())

        events_panel = tk.Frame(lower, bg=WHITE, padx=13, pady=12,
                                highlightthickness=1, highlightbackground=BORDER)
        events_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        events_panel.rowconfigure(1, weight=1)
        events_panel.columnconfigure(0, weight=1)
        tk.Label(events_panel, text="Status changes", bg=WHITE, fg=NAVY,
                 font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 10))
        event_frame = tk.Frame(events_panel, bg=WHITE)
        event_frame.grid(row=1, column=0, sticky="nsew")
        event_frame.rowconfigure(0, weight=1)
        event_frame.columnconfigure(0, weight=1)
        self.events = ttk.Treeview(event_frame, columns=("time", "host", "event"),
                                   show="headings", style="Events.Treeview", selectmode="browse")
        for key, caption, width in (("time", "TIME", 65), ("host", "DESTINATION", 145),
                                    ("event", "EVENT", 165)):
            self.events.heading(key, text=caption)
            self.events.column(key, width=width, minwidth=55, anchor="w", stretch=(key in ("host", "event")))
        self.events.tag_configure("up", foreground=GREEN)
        self.events.tag_configure("down", foreground=RED)
        self.events.tag_configure("system", foreground=MUTED)
        self.events.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(event_frame, orient="vertical", command=self.events.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.events.configure(yscrollcommand=scroll.set)

    def _add_entry(self) -> None:
        raw = self.target_var.get().strip()
        if not raw:
            self.hint_var.set("Enter an IPv4 address, IPv6 address or hostname.")
            return
        self._add_targets(re.split(r"[,;\s]+", raw))
        self.target_var.set("")

    def _show_bulk_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Paste destinations")
        dialog.geometry("490x375")
        dialog.minsize(375, 290)
        dialog.transient(self)
        dialog.configure(bg=BG)
        tk.Label(dialog, text="Add multiple destinations", bg=BG, fg=NAVY,
                 font=("Arial", 14, "bold")).pack(anchor="w", padx=16, pady=(16, 5))
        tk.Label(dialog, text="One per line, or separated by commas, spaces or tabs.",
                 bg=BG, fg=MUTED).pack(anchor="w", padx=16)
        entry = tk.Text(dialog, wrap="none", font=("Consolas", 11), height=10,
                        bg=WHITE, fg=TEXT, relief="flat", padx=10, pady=9)
        entry.pack(fill="both", expand=True, padx=16, pady=(12, 9))
        footer = tk.Frame(dialog, bg=BG)
        footer.pack(fill="x", padx=16, pady=(0, 14))

        def confirm(event=None):
            raw = entry.get("1.0", "end").strip()
            if not raw:
                return
            self._add_targets(re.split(r"[,;\s]+", raw))
            dialog.destroy()

        ttk.Button(footer, text="Cancel", style="Regular.TButton", command=dialog.destroy).pack(side="right")
        ttk.Button(footer, text="Add destinations", style="Accent.TButton", command=confirm).pack(
            side="right", padx=(0, 7))
        entry.bind("<Control-Return>", confirm)
        entry.focus_set()

    def _add_targets(self, values, persist=True, notify=True) -> None:
        added = 0
        invalid = []
        duplicates = 0
        for raw in values:
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                target = validate_target(raw)
            except ValueError:
                invalid.append(str(raw)[:80])
                continue
            if target in self.stats:
                duplicates += 1
                continue
            if len(self.stats) >= MAX_TARGETS:
                invalid.append(f"Maximum of {MAX_TARGETS} destinations reached")
                break
            self.stats[target] = PingStats()
            self.tree.insert("", "end", iid=target, values=self._tree_values(target), tags=("waiting",))
            added += 1
            if self.running:
                self._start_worker(target)
        if not self.selected_target and self.stats:
            target = next(iter(self.stats))
            self.tree.selection_set(target)
            self.tree.focus(target)
            self._selection_changed()
        self._refresh_overview()
        self._refresh_buttons()
        if persist:
            self._save_config()
        if notify:
            self.hint_var.set(f"Added {added} destination(s)." +
                              (f" Skipped {duplicates} duplicate(s)." if duplicates else ""))
            if invalid:
                messagebox.showwarning("Some destinations were skipped",
                                       "Invalid entries or limit reached:\n" +
                                       "\n".join(invalid[:7]) +
                                       (f"\n...and {len(invalid)-7} more" if len(invalid) > 7 else ""),
                                       parent=self)

    def _tree_values(self, target: str) -> tuple:
        s = self.stats[target]
        if s.last_success is None:
            status = "○  Waiting"
        elif s.last_success:
            status = "●  Reachable"
        else:
            status = "●  Unreachable"
        return (target, status, pretty_latency(s.last_latency_ms), pretty_latency(s.avg_latency_ms),
                pretty_latency(s.min_latency_ms), pretty_latency(s.max_latency_ms),
                f"{s.loss_pct:.1f}%" if s.sent else "—", str(s.sent),
                s.last_checked.strftime("%H:%M:%S") if s.last_checked else "—")

    def _refresh_overview(self) -> None:
        counts = list(self.stats.values())
        self._card_values["total"].configure(text=str(len(counts)))
        self._card_values["up"].configure(text=str(sum(s.last_success is True for s in counts)))
        self._card_values["down"].configure(text=str(sum(s.last_success is False for s in counts)))
        measured = [s for s in counts if s.measured]
        total_measured = sum(s.measured for s in measured)
        avg = sum(s.total_latency_ms for s in measured) / total_measured if total_measured else None
        self._card_values["latency"].configure(text=pretty_latency(avg))

    def _refresh_buttons(self) -> None:
        self.start_btn.configure(state="disabled" if self.running else "normal")
        self.stop_btn.configure(state="normal" if self.running else "disabled")
        self.interval_spin.configure(state="disabled" if self.running else "normal")
        self.timeout_spin.configure(state="disabled" if self.running else "normal")
        self.remove_btn.configure(state="normal" if self.tree.selection() else "disabled")

    def _start(self) -> None:
        if self.running:
            return
        if not self.stats:
            messagebox.showinfo("No destinations", "Add one or more destinations first.", parent=self)
            return
        try:
            interval = float(self.interval_var.get())
            timeout = float(self.timeout_var.get())
            if not (1 <= interval <= 60 and 0.5 <= timeout <= 10):
                raise ValueError()
        except ValueError:
            messagebox.showerror("Invalid settings", "Interval must be 1–60 seconds and timeout 0.5–10 seconds.",
                                 parent=self)
            return
        self.active_interval = interval
        self.active_timeout = timeout
        self.running = True
        self.generation += 1
        self.app_state_var.set("●  MONITORING")
        for target in self.stats:
            self._start_worker(target)
        self._refresh_buttons()
        self._save_config()
        self._event("—", "Monitoring started", "system")
        self.hint_var.set(f"Checking {len(self.stats)} destination(s) every {interval:g} s, "
                          f"with a {timeout:g} s timeout.")

    def _start_worker(self, target: str) -> None:
        if not self.running:
            return
        stopped = threading.Event()
        generation = self.generation
        thread = threading.Thread(target=self._ping_worker,
                                  args=(target, stopped, generation, self.active_interval, self.active_timeout),
                                  name=f"ping-{target}", daemon=True)
        self.workers[target] = (thread, stopped)
        thread.start()

    def _ping_worker(self, target: str, stopped: threading.Event, generation: int,
                     interval: float, timeout: float) -> None:
        while not stopped.is_set():
            result = ping_once(target, timeout)
            if stopped.is_set():
                return
            self.pending.put((generation, result))
            if stopped.wait(interval):
                return

    def _stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self.generation += 1  # Ignore late results from the previous monitoring session.
        for _, stop in self.workers.values():
            stop.set()
        self.workers.clear()
        self.app_state_var.set("●  PAUSED")
        self._refresh_buttons()
        self._event("—", "Monitoring paused", "system")
        self.hint_var.set("Monitoring paused. Statistics are preserved until cleared.")

    def _drain_results(self) -> None:
        # All Tkinter state is read/written here, on the main thread.
        for _ in range(200):
            try:
                generation, result = self.pending.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation or not self.running or result.target not in self.stats:
                continue
            s = self.stats[result.target]
            previous = s.last_success
            previous_failures = s.consecutive_failures
            s.record(result)
            self.samples.append(result)
            self.tree.item(result.target, values=self._tree_values(result.target),
                           tags=("up" if result.success else "down",))
            if previous is None or previous != result.success:
                if result.success and previous is False:
                    message = f"Recovered ({previous_failures} lost)"
                    self._event(result.target, message, "up")
                elif result.success:
                    self._event(result.target, "Reachable", "up")
                else:
                    self._event(result.target, result.detail, "down")
            if self.selected_target == result.target:
                self._redraw_graph()
        self._refresh_overview()
        self.after(125, self._drain_results)

    def _selection_changed(self, event=None) -> None:
        selection = self.tree.selection()
        self.selected_target = selection[0] if selection else None
        self._refresh_buttons()
        self._redraw_graph()
        if self.selected_target and self.selected_target in self.stats:
            s = self.stats[self.selected_target]
            self.hint_var.set(f"{self.selected_target} — {s.last_detail}")

    def _remove(self) -> None:
        to_remove = list(self.tree.selection())
        if not to_remove:
            return
        for target in to_remove:
            worker = self.workers.pop(target, None)
            if worker:
                worker[1].set()
            self.stats.pop(target, None)
            self.tree.delete(target)
        if self.stats:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self._selection_changed()
        else:
            self.selected_target = None
            if self.running:
                self._stop()
            self._redraw_graph()
        self._refresh_overview()
        self._refresh_buttons()
        self._save_config()
        self.hint_var.set(f"Removed {len(to_remove)} destination(s).")

    def _clear_stats(self) -> None:
        for target in self.stats:
            self.stats[target] = PingStats()
            self.tree.item(target, values=self._tree_values(target), tags=("waiting",))
        self.samples.clear()
        for item in self.events.get_children():
            self.events.delete(item)
        self._refresh_overview()
        self._redraw_graph()
        self.hint_var.set("Statistics and in-memory samples cleared. Monitoring continues if started.")

    def _event(self, target: str, event_text: str, kind: str) -> None:
        self.events.insert("", 0, values=(datetime.now().strftime("%H:%M:%S"), target, event_text),
                           tags=(kind,))
        children = self.events.get_children()
        if len(children) > 250:
            self.events.delete(*children[250:])

    def _sort_by(self, column: str) -> None:
        reverse = not self._sort_reverse if column == self._sort_col else False
        self._sort_col = column
        self._sort_reverse = reverse

        def value(target: str):
            s = self.stats[target]
            fields = {"target": target,
                      "state": (1 if s.last_success else 0) if s.last_success is not None else -1,
                      "last": s.last_latency_ms, "avg": s.avg_latency_ms,
                      "min": s.min_latency_ms, "max": s.max_latency_ms,
                      "loss": s.loss_pct if s.sent else None, "sent": s.sent,
                      "checked": s.last_checked.timestamp() if s.last_checked else None}
            result = fields[column]
            return (result is not None, result if result is not None else -1)

        targets = sorted(self.stats, key=value, reverse=reverse)
        for index, target in enumerate(targets):
            self.tree.move(target, "", index)

    def _redraw_graph(self) -> None:
        if not hasattr(self, "graph"):
            return
        chart = self.graph
        chart.delete("all")
        width = max(chart.winfo_width(), 250)
        height = max(chart.winfo_height(), 105)
        left, right, top, bottom = 45, width - 14, 14, height - 25
        chart.create_line(left, bottom, right, bottom, fill=BORDER)
        target = self.selected_target
        if not target or target not in self.stats:
            self.chart_title_var.set("Latency history")
            self.chart_subtitle_var.set("Select a destination to see its last 60 checks.")
            chart.create_text(width / 2, height / 2, text="No destination selected", fill=MUTED,
                              font=("Arial", 10))
            return
        s = self.stats[target]
        self.chart_title_var.set(f"Latency history  ·  {target}")
        self.chart_subtitle_var.set(f"Last {len(s.history)} checks  |  "
                                    f"Packet loss: {s.loss_pct:.1f}%  |  "
                                    f"{s.last_detail}")
        observations = list(s.history)
        if not observations:
            chart.create_text(width / 2, height / 2, text="Start monitoring to collect latency samples",
                              fill=MUTED, font=("Arial", 10))
            return
        successful = [v for v in observations if v is not None]
        max_ms = max(10.0, max(successful, default=0.0) * 1.15)
        for part in (0, 0.5, 1):
            y = bottom - (bottom - top) * part
            chart.create_line(left, y, right, y, fill="#e7edf4", dash=(3, 4))
            chart.create_text(left - 6, y, text=f"{max_ms * part:g}", anchor="e",
                              fill=MUTED, font=("Arial", 8))
        chart.create_text(left, height - 7, text="Oldest", anchor="w", fill=MUTED, font=("Arial", 8))
        chart.create_text(right, height - 7, text="Newest", anchor="e", fill=MUTED, font=("Arial", 8))
        last_point = None
        for index, reading in enumerate(observations):
            x = left + index * (right - left) / max(1, len(observations) - 1)
            if reading is None:
                chart.create_line(x - 3, bottom - 3, x + 3, bottom + 3, fill=RED, width=2)
                chart.create_line(x - 3, bottom + 3, x + 3, bottom - 3, fill=RED, width=2)
                last_point = None
                continue
            y = bottom - (bottom - top) * reading / max_ms
            if last_point is not None:
                chart.create_line(last_point[0], last_point[1], x, y, fill=TEAL, width=2)
            chart.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5,
                              outline=WHITE, fill=TEAL)
            last_point = (x, y)

    def _export(self) -> None:
        if not self.samples:
            messagebox.showinfo("No samples", "Start monitoring to collect data before exporting.", parent=self)
            return
        name = f"multipinger-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(parent=self, title="Export ping samples", initialfile=name,
                                            defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as outfile:
                writer = csv.writer(outfile)
                writer.writerow(("timestamp", "destination", "reachable", "latency_ms", "detail"))
                for sample in self.samples:
                    writer.writerow((sample.timestamp.isoformat(timespec="seconds"), sample.target,
                                     sample.success, f"{sample.latency_ms:.3f}" if sample.latency_ms is not None else "",
                                     sample.detail))
        except OSError as error:
            messagebox.showerror("Export failed", str(error), parent=self)
            return
        self.hint_var.set(f"Exported {len(self.samples):,} samples to {path}.")

    def _save_config(self) -> None:
        path = config_path()
        preferences = {"targets": list(self.stats),
                       "interval": self._in_range(self.interval_var.get(), 2, 1, 60),
                       "timeout": self._in_range(self.timeout_var.get(), 1.5, 0.5, 10)}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = path.with_suffix(".tmp")
            temporary_path.write_text(json.dumps(preferences, indent=2), encoding="utf-8")
            temporary_path.replace(path)
        except OSError as error:
            self.hint_var.set(f"Could not save preferences: {error}")

    def _close(self) -> None:
        for _, stop in self.workers.values():
            stop.set()
        self.workers.clear()
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    # Preserve crisp text on high-DPI Windows displays, as in the original app.
    if platform.system() == "Windows":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except (ImportError, AttributeError, OSError):
            pass
    MultiPinger().mainloop()
