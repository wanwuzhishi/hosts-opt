#!/usr/bin/env python3
"""hosts-opt 图形界面。

用法:
    python hosts_opt_gui.py

界面只负责编排，实际拉取/测速/写入仍由 hosts_opt.py 在子进程中完成。
"""

from __future__ import annotations

import ctypes
import concurrent.futures
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import hosts_opt

ROOT = hosts_opt.ROOT
CORE = ROOT / "hosts_opt.py"
REPORT = hosts_opt.DIST_DIR / "report.json"
GENERATED = hosts_opt.DIST_DIR / "hosts.generated"
LOG_FILE = hosts_opt.LOG_DIR / "hosts_opt.log"
INSTALL_SCRIPT = ROOT / "install-task.ps1"
UNINSTALL_SCRIPT = ROOT / "uninstall-task.ps1"
TASK_NAME = "HostsOpt"

COLORS = {
    "bg": "#f3f5f9",
    "card": "#ffffff",
    "border": "#e2e8f0",
    "text": "#0f172a",
    "muted": "#64748b",
    "primary": "#2563eb",
    "primary_active": "#1d4ed8",
    "success": "#16a34a",
    "success_active": "#15803d",
    "warning": "#f59e0b",
    "warning_active": "#d97706",
    "header": "#0f172a",
    "header_muted": "#94a3b8",
    "console": "#0b1220",
}


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def core_python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def gui_python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def core_command(args: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--cli", *args]
    return [core_python(), str(CORE), *args]


def hide_console() -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def command_text(parts: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in parts)


def open_path(path: Path) -> None:
    if not path.exists():
        messagebox.showwarning("文件不存在", str(path))
        return
    try:
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        messagebox.showerror("无法打开", f"{path}\n\n{exc}")


class HostsOptApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = hosts_opt.load_config(hosts_opt.DEFAULT_CONFIG)
        self.busy = False
        self.proc: subprocess.Popen | None = None
        self.queue: queue.Queue = queue.Queue()
        self.report: dict | None = None
        self.buttons: list[tk.Button] = []
        self.option_widgets: list[tk.Widget] = []
        self.task_installed: bool | None = None
        self.checking = False
        self.conn_targets: dict[str, str] = {}
        self.conn_from_managed = False

        self._configure_window()
        self._build_fonts()
        self._build_ui()
        self._load_log_tail()
        self.load_report()
        self.refresh_status()
        self._load_backups()
        self._refresh_task_async()
        self.root.after(100, self._poll_queue)
        if self.conn_from_managed:
            self.root.after(1200, self._start_connectivity_check)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------

    def _configure_window(self) -> None:
        self.root.title("hosts-opt · GitHub Hosts 优化器")
        self.root.configure(bg=COLORS["bg"])
        width, height = 1140, 840
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(980, 700)
        try:
            self.root.tk.call(
                "tk", "scaling", self.root.winfo_fpixels("1i") / 72.0
            )
        except Exception:
            pass

    def _build_fonts(self) -> None:
        families = set(tkfont.families(self.root))

        def pick(*names: str) -> str:
            for name in names:
                if name in families:
                    return name
            return "TkDefaultFont"

        self.family = pick(
            "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial"
        )
        self.font_title = (self.family, 22, "bold")
        self.font_subtitle = (self.family, 10)
        self.font_body = (self.family, 10)
        self.font_small = (self.family, 9)
        self.font_bold = (self.family, 10, "bold")
        self.font_stat = (self.family, 11, "bold")
        self.font_mono = (
            ("Consolas", 9)
            if "Consolas" in families
            else ("Courier New", 9)
        )
        self.root.option_add("*Font", self.font_body)

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(
            "Treeview",
            rowheight=26,
            font=self.font_body,
            fieldbackground=COLORS["card"],
        )
        style.configure("Treeview.Heading", font=self.font_bold)
        style.configure(
            "TNotebook.Tab", padding=(14, 8), font=self.font_body
        )

        self._build_header()

        main = tk.Frame(self.root, bg=COLORS["bg"])
        main.pack(fill="both", expand=True, padx=18, pady=14)

        self._build_status_card(main)
        self._build_action_card(main)
        self._build_notebook(main)
        self._build_bottom_bar(main)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=COLORS["header"])
        header.pack(fill="x")
        inner = tk.Frame(header, bg=COLORS["header"])
        inner.pack(fill="x", padx=24, pady=18)

        left = tk.Frame(inner, bg=COLORS["header"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(
            left,
            text="hosts-opt",
            bg=COLORS["header"],
            fg="white",
            font=self.font_title,
        ).pack(anchor="w")
        tk.Label(
            left,
            text="GitHub Hosts 优化器   ·   多源拉取   ·   本地 TLS 测速   ·   定时更新",
            bg=COLORS["header"],
            fg=COLORS["header_muted"],
            font=self.font_subtitle,
        ).pack(anchor="w", pady=(4, 0))

        right = tk.Frame(inner, bg=COLORS["header"])
        right.pack(side="right")
        self.admin_badge = tk.Label(
            right,
            text="检查权限…",
            bg=COLORS["warning"],
            fg="#111827",
            font=self.font_bold,
            padx=12,
            pady=5,
        )
        self.admin_badge.pack(anchor="e")
        self.elevate_btn = tk.Button(
            right,
            text="以管理员身份重启",
            command=self.elevate,
            bg=COLORS["warning"],
            fg="#111827",
            activebackground=COLORS["warning_active"],
            activeforeground="#111827",
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
            font=self.font_small,
            takefocus=False,
        )
        self.elevate_btn.pack(anchor="e", pady=(8, 0))
        self.elevate_btn.bind(
            "<Enter>",
            lambda _event: self.elevate_btn.configure(
                bg=COLORS["warning_active"]
            ),
        )
        self.elevate_btn.bind(
            "<Leave>",
            lambda _event: self.elevate_btn.configure(bg=COLORS["warning"]),
        )
        self.buttons.append(self.elevate_btn)

    def _build_status_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent,
            bg=COLORS["card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 12))
        grid = tk.Frame(card, bg=COLORS["card"])
        grid.pack(fill="x", padx=18, pady=14)
        grid.columnconfigure(0, weight=2)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(2, weight=1)
        grid.columnconfigure(3, weight=1)

        self.stat_hosts = self._stat(
            grid, 0, "系统 hosts", "—", wraplength=440
        )
        self.stat_managed = self._stat(grid, 1, "托管条目", "—")
        self.stat_generated = self._stat(grid, 2, "上次生成", "—")
        self.stat_task = self._stat(grid, 3, "定时任务", "查询中…")

    def _stat(
        self,
        parent: tk.Widget,
        column: int,
        title: str,
        initial: str,
        wraplength: int = 0,
    ) -> tk.Label:
        frame = tk.Frame(parent, bg=COLORS["card"])
        frame.grid(
            row=0,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 20, 0),
        )
        tk.Label(
            frame,
            text=title,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.font_small,
        ).pack(anchor="w")
        value = tk.Label(
            frame,
            text=initial,
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.font_stat,
            anchor="w",
            justify="left",
            wraplength=wraplength or 0,
        )
        value.pack(anchor="w", pady=(4, 0))
        return value

    def _build_action_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(
            parent,
            bg=COLORS["card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 12))

        row1 = tk.Frame(card, bg=COLORS["card"])
        row1.pack(fill="x", padx=18, pady=(14, 6))
        tk.Label(
            row1,
            text="操作",
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.font_bold,
        ).pack(side="left", padx=(0, 16))
        self.update_btn = self._button(
            row1,
            "拉取并测速",
            self.run_update,
            COLORS["primary"],
            COLORS["primary_active"],
        )
        self.update_btn.pack(side="left", padx=(0, 8))
        self.apply_btn = self._button(
            row1,
            "应用 hosts",
            self.run_apply,
            COLORS["success"],
            COLORS["success_active"],
        )
        self.apply_btn.pack(side="left", padx=(0, 8))
        self.restore_btn = self._button(
            row1,
            "移除托管块",
            self.run_restore,
            "#e2e8f0",
            "#cbd5e1",
            fg=COLORS["text"],
        )
        self.restore_btn.pack(side="left", padx=(0, 8))

        tk.Label(
            row1,
            text="应用会重新拉取测速后写入系统 hosts",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.font_small,
        ).pack(side="left", padx=(10, 0))

        row2 = tk.Frame(card, bg=COLORS["card"])
        row2.pack(fill="x", padx=18, pady=(0, 14))

        self.offline_var = tk.BooleanVar(value=False)
        self.nodoh_var = tk.BooleanVar(value=False)
        self.pools_var = tk.BooleanVar(
            value=any(
                pool.get("enabled", True)
                for pool in self.cfg.get("ip_pools", [])
            )
        )
        self.offline_check = tk.Checkbutton(
            row2,
            text="离线模式（只用缓存源）",
            variable=self.offline_var,
            bg=COLORS["card"],
            activebackground=COLORS["card"],
            selectcolor=COLORS["card"],
            fg=COLORS["text"],
            font=self.font_small,
            takefocus=False,
        )
        self.offline_check.pack(side="left")
        self.pools_check = tk.Checkbutton(
            row2,
            text="IP 优选（CF/Vercel）",
            variable=self.pools_var,
            command=self._sync_pool_controls,
            bg=COLORS["card"],
            activebackground=COLORS["card"],
            selectcolor=COLORS["card"],
            fg=COLORS["text"],
            font=self.font_small,
            takefocus=False,
        )
        self.pools_check.pack(side="left", padx=(14, 0))
        self.notion_var = tk.BooleanVar(value=False)
        self.notion_check = tk.Checkbutton(
            row2,
            text="Notion 代理（第三方）",
            variable=self.notion_var,
            bg=COLORS["card"],
            activebackground=COLORS["card"],
            selectcolor=COLORS["card"],
            fg=COLORS["text"],
            font=self.font_small,
            takefocus=False,
        )
        self.notion_check.pack(side="left", padx=(14, 0))
        self.nodoh_check = tk.Checkbutton(
            row2,
            text="禁用 DoH 补候选",
            variable=self.nodoh_var,
            bg=COLORS["card"],
            activebackground=COLORS["card"],
            selectcolor=COLORS["card"],
            fg=COLORS["text"],
            font=self.font_small,
            takefocus=False,
        )
        self.nodoh_check.pack(side="left", padx=(14, 0))
        self.option_widgets.extend(
            [
                self.offline_check,
                self.pools_check,
                self.notion_check,
                self.nodoh_check,
            ]
        )

        tk.Label(
            row2,
            text="定时刷新",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.font_small,
        ).pack(side="left", padx=(26, 6))
        self.interval_var = tk.StringVar(value="1")
        self.interval_spin = tk.Spinbox(
            row2,
            from_=1,
            to=24,
            width=4,
            textvariable=self.interval_var,
            font=self.font_body,
            relief="solid",
            bd=1,
            justify="center",
            takefocus=False,
        )
        self.interval_spin.pack(side="left")
        self.option_widgets.append(self.interval_spin)
        tk.Label(
            row2,
            text="小时",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.font_small,
        ).pack(side="left", padx=(4, 10))

        self.install_btn = self._button(
            row2,
            "安装任务",
            self.run_install,
            "#e2e8f0",
            "#cbd5e1",
            fg=COLORS["text"],
        )
        self.install_btn.pack(side="left", padx=(0, 6))
        self.uninstall_btn = self._button(
            row2,
            "卸载任务",
            self.run_uninstall,
            "#e2e8f0",
            "#cbd5e1",
            fg=COLORS["text"],
        )
        self.uninstall_btn.pack(side="left")
        self._sync_pool_controls()

    def _build_notebook(self, parent: tk.Widget) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill="both", expand=True)

        self.results_tab = tk.Frame(self.notebook, bg=COLORS["card"])
        self.connectivity_tab = tk.Frame(self.notebook, bg=COLORS["card"])
        self.candidates_tab = tk.Frame(self.notebook, bg=COLORS["card"])
        self.backup_tab = tk.Frame(self.notebook, bg=COLORS["card"])
        self.log_tab = tk.Frame(self.notebook, bg=COLORS["console"])
        self.notebook.add(self.results_tab, text="  选中结果  ")
        self.notebook.add(self.connectivity_tab, text="  连通性  ")
        self.notebook.add(self.candidates_tab, text="  候选明细  ")
        self.notebook.add(self.backup_tab, text="  备份/回退  ")
        self.notebook.add(self.log_tab, text="  运行日志  ")

        self._build_results_tab()
        self._build_connectivity_tab()
        self._build_candidates_tab()
        self._build_backup_tab()
        self._build_log_tab()

    def _build_results_tab(self) -> None:
        frame = tk.Frame(self.results_tab, bg=COLORS["card"])
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        columns = ("domain", "ip", "median", "ok", "kind", "origins")
        self.results_tree = ttk.Treeview(
            frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "domain": ("域名", 300, "w"),
            "ip": ("选中 IP", 150, "center"),
            "median": ("延迟", 90, "e"),
            "ok": ("采样成功", 90, "center"),
            "kind": ("类型", 100, "center"),
            "origins": ("来源", 280, "w"),
        }
        for column, (title, width, anchor) in headings.items():
            self.results_tree.heading(column, text=title, anchor=anchor)
            self.results_tree.column(
                column,
                width=width,
                anchor=anchor,
                stretch=column == "origins",
            )
        self.results_tree.tag_configure("odd", background="#f8fafc")
        self.results_tree.tag_configure("even", background="#ffffff")
        self.results_tree.tag_configure("warn", foreground="#b45309")
        self.results_tree.tag_configure(
            "selected", background="#ecfdf5", foreground="#065f46"
        )
        self.results_tree.bind("<Double-1>", self._copy_tree_ip)
        self.results_tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self.results_tree.yview
        )
        self.results_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.empty_label = tk.Label(
            frame,
            text="还没有测速结果\n点击上方“拉取并测速”开始",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.font_body,
            justify="center",
        )
        self.empty_label.grid(row=0, column=0, sticky="nsew")

    def _build_connectivity_tab(self) -> None:
        frame = tk.Frame(self.connectivity_tab, bg=COLORS["card"])
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        top = tk.Frame(frame, bg=COLORS["card"])
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        self.conn_source_var = tk.StringVar(value="检测对象：—")
        tk.Label(
            top,
            textvariable=self.conn_source_var,
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.font_small,
        ).pack(side="left")

        self.conn_button = tk.Button(
            top,
            text="检测全部",
            command=self._start_connectivity_check,
            bg=COLORS["primary"],
            fg="white",
            activebackground=COLORS["primary_active"],
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            font=self.font_bold,
            disabledforeground="#94a3b8",
            takefocus=False,
        )
        self.conn_button.pack(side="right")

        self.conn_summary_var = tk.StringVar(value="尚未检测")
        tk.Label(
            top,
            textvariable=self.conn_summary_var,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.font_small,
        ).pack(side="right", padx=(0, 12))

        columns = ("domain", "ip", "latency", "status", "samples", "error")
        self.conn_tree = ttk.Treeview(
            frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "domain": ("域名", 300, "w"),
            "ip": ("当前 IP", 150, "center"),
            "latency": ("延迟(ms)", 90, "e"),
            "status": ("连通性", 80, "center"),
            "samples": ("采样", 80, "center"),
            "error": ("说明", 340, "w"),
        }
        for column, (title, width, anchor) in headings.items():
            self.conn_tree.heading(column, text=title, anchor=anchor)
            self.conn_tree.column(
                column,
                width=width,
                anchor=anchor,
                stretch=column == "error",
            )
        self.conn_tree.tag_configure("pending", foreground=COLORS["muted"])
        self.conn_tree.tag_configure(
            "ok", background="#ecfdf5", foreground="#065f46"
        )
        self.conn_tree.tag_configure(
            "warn", background="#fffbeb", foreground="#92400e"
        )
        self.conn_tree.tag_configure(
            "fail", background="#fef2f2", foreground="#b91c1c"
        )
        self.conn_tree.bind("<Double-1>", self._copy_tree_ip)
        self.conn_tree.grid(row=1, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self.conn_tree.yview
        )
        self.conn_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=1, column=1, sticky="ns")

    def _build_candidates_tab(self) -> None:
        frame = tk.Frame(self.candidates_tab, bg=COLORS["card"])
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        columns = (
            "domain",
            "ip",
            "result",
            "median",
            "success",
            "origins",
            "error",
        )
        self.candidates_tree = ttk.Treeview(
            frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "domain": ("域名", 280, "w"),
            "ip": ("候选 IP", 145, "center"),
            "result": ("结果", 70, "center"),
            "median": ("中位延迟", 90, "e"),
            "success": ("成功/采样", 90, "center"),
            "origins": ("来源", 210, "w"),
            "error": ("错误", 320, "w"),
        }
        for column, (title, width, anchor) in headings.items():
            self.candidates_tree.heading(column, text=title, anchor=anchor)
            self.candidates_tree.column(
                column,
                width=width,
                anchor=anchor,
                stretch=column in ("origins", "error"),
            )
        self.candidates_tree.tag_configure("odd", background="#f8fafc")
        self.candidates_tree.tag_configure("even", background="#ffffff")
        self.candidates_tree.tag_configure(
            "fail", background="#fef2f2", foreground="#b91c1c"
        )
        self.candidates_tree.tag_configure(
            "selected", background="#ecfdf5", foreground="#065f46"
        )
        self.candidates_tree.bind("<Double-1>", self._copy_tree_ip)
        self.candidates_tree.grid(row=0, column=0, sticky="nsew")

        y_scroll = ttk.Scrollbar(
            frame, orient="vertical", command=self.candidates_tree.yview
        )
        self.candidates_tree.configure(yscrollcommand=y_scroll.set)
        y_scroll.grid(row=0, column=1, sticky="ns")

        x_scroll = ttk.Scrollbar(
            frame, orient="horizontal", command=self.candidates_tree.xview
        )
        self.candidates_tree.configure(xscrollcommand=x_scroll.set)
        x_scroll.grid(row=1, column=0, sticky="ew")

    def _build_backup_tab(self) -> None:
        frame = tk.Frame(self.backup_tab, bg=COLORS["card"])
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.rowconfigure(2, weight=1)
        frame.columnconfigure(0, weight=1)

        top = tk.Frame(frame, bg=COLORS["card"])
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        self.backup_original_var = tk.StringVar(value="原始备份：—")
        tk.Label(
            top,
            textvariable=self.backup_original_var,
            bg=COLORS["card"],
            fg=COLORS["text"],
            font=self.font_small,
        ).pack(side="left")

        self.rollback_selected_btn = self._button(
            top,
            "回退到选中",
            self.run_rollback_selected,
            "#e2e8f0",
            "#cbd5e1",
            fg=COLORS["text"],
        )
        self.rollback_selected_btn.pack(side="right", padx=(6, 0))
        self.rollback_original_btn = self._button(
            top,
            "回退到原始",
            self.run_rollback_original,
            COLORS["warning"],
            COLORS["warning_active"],
            fg="#111827",
        )
        self.rollback_original_btn.pack(side="right", padx=(6, 0))
        self.backup_now_btn = self._button(
            top,
            "立即备份",
            self.run_backup,
            COLORS["primary"],
            COLORS["primary_active"],
        )
        self.backup_now_btn.pack(side="right")

        self.backup_summary_var = tk.StringVar(value="")
        tk.Label(
            frame,
            textvariable=self.backup_summary_var,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=self.font_small,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        columns = ("name", "modified", "size", "type")
        self.backup_tree = ttk.Treeview(
            frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "name": ("备份文件", 360, "w"),
            "modified": ("时间", 170, "center"),
            "size": ("大小", 100, "e"),
            "type": ("类型", 90, "center"),
        }
        for column, (title, width, anchor) in headings.items():
            self.backup_tree.heading(column, text=title, anchor=anchor)
            self.backup_tree.column(
                column,
                width=width,
                anchor=anchor,
                stretch=column == "name",
            )
        self.backup_tree.tag_configure(
            "original", background="#ecfdf5", foreground="#065f46"
        )
        self.backup_tree.grid(row=2, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self.backup_tree.yview
        )
        self.backup_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=2, column=1, sticky="ns")

    def _build_log_tab(self) -> None:
        frame = tk.Frame(self.log_tab, bg=COLORS["console"])
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            frame,
            bg=COLORS["console"],
            fg="#d1d5db",
            insertbackground="#ffffff",
            relief="flat",
            bd=0,
            font=self.font_mono,
            wrap="word",
            padx=12,
            pady=10,
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.tag_configure("error", foreground="#fca5a5")
        self.log_text.tag_configure("warn", foreground="#fcd34d")
        self.log_text.tag_configure("ok", foreground="#86efac")
        self.log_text.tag_configure("cmd", foreground="#93c5fd")
        self.log_text.tag_configure("muted", foreground="#64748b")

        scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

    def _build_bottom_bar(self, parent: tk.Widget) -> None:
        bottom = tk.Frame(parent, bg=COLORS["bg"])
        bottom.pack(fill="x", pady=(10, 0))

        self.progress = ttk.Progressbar(
            bottom, mode="determinate", length=220, maximum=100, value=0
        )
        self.progress.pack(side="left")
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(
            bottom,
            textvariable=self.status_var,
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=self.font_small,
        ).pack(side="left", padx=12)

        links = tk.Frame(bottom, bg=COLORS["bg"])
        links.pack(side="right")
        for text, path in (
            ("刷新状态", None),
            ("打开生成文件", GENERATED),
            ("测速报告", REPORT),
            ("运行日志", LOG_FILE),
            ("配置", ROOT / "config.json"),
        ):
            if path is None:
                command = self.refresh_status
            else:
                command = lambda p=path: open_path(p)
            tk.Button(
                links,
                text=text,
                command=command,
                bg=COLORS["bg"],
                fg=COLORS["primary"],
                activebackground=COLORS["bg"],
                activeforeground=COLORS["primary_active"],
                relief="flat",
                bd=0,
                padx=8,
                pady=2,
                cursor="hand2",
                font=self.font_small,
                takefocus=False,
            ).pack(side="left", padx=(6, 0))

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command,
        base_bg: str,
        hover_bg: str,
        fg: str = "white",
    ) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=base_bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground=fg,
            relief="flat",
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            font=self.font_bold,
            disabledforeground="#94a3b8",
            takefocus=False,
        )
        button._base_bg = base_bg  # type: ignore[attr-defined]
        button._hover_bg = hover_bg  # type: ignore[attr-defined]

        def on_enter(_event, widget=button) -> None:
            if str(widget["state"]) != "disabled":
                widget.configure(bg=widget._hover_bg)  # type: ignore[attr-defined]

        def on_leave(_event, widget=button) -> None:
            widget.configure(bg=widget._base_bg)  # type: ignore[attr-defined]

        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", on_leave)
        self.buttons.append(button)
        return button

    # ------------------------------------------------------------------
    # 状态与结果
    # ------------------------------------------------------------------

    def refresh_status(self) -> None:
        try:
            self.cfg = hosts_opt.load_config(hosts_opt.DEFAULT_CONFIG)
        except Exception:
            pass

        admin = hosts_opt.is_admin()
        if admin:
            self.admin_badge.configure(
                text="管理员权限", bg="#10b981", fg="white"
            )
            self.elevate_btn.pack_forget()
        else:
            self.admin_badge.configure(
                text="非管理员", bg=COLORS["warning"], fg="#111827"
            )
            if not self.elevate_btn.winfo_ismapped():
                self.elevate_btn.pack(anchor="e", pady=(8, 0))

        hosts_path = hosts_opt.default_hosts_path(self.cfg)
        self.stat_hosts.configure(text=str(hosts_path))

        text = ""
        try:
            text = hosts_opt._read_text(hosts_path)
        except Exception:
            pass
        entries = hosts_opt.managed_entries(text) if text else {}
        if entries:
            self.stat_managed.configure(text=f"{len(entries)} 个域名")
        elif hosts_opt.BEGIN_MARK in text:
            self.stat_managed.configure(text="块不完整")
        else:
            self.stat_managed.configure(text="未写入")

        if GENERATED.exists():
            modified = datetime.fromtimestamp(
                GENERATED.stat().st_mtime
            ).strftime("%m-%d %H:%M:%S")
            self.stat_generated.configure(text=modified)
        else:
            self.stat_generated.configure(text="尚未生成")

        if self.task_installed is True:
            self.stat_task.configure(text="已安装")
        elif self.task_installed is False:
            self.stat_task.configure(text="未安装")
        else:
            self.stat_task.configure(text="查询中…")

    def _refresh_task_async(self) -> None:
        if os.name != "nt":
            self.task_installed = None
            return

        def worker() -> None:
            try:
                flags = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(
                    ["schtasks", "/Query", "/TN", TASK_NAME],
                    capture_output=True,
                    creationflags=flags,
                    timeout=10,
                )
                installed = result.returncode == 0
            except Exception:
                installed = None
            self.queue.put(("task", installed))

        threading.Thread(target=worker, daemon=True).start()

    def _set_task_status(self, installed: bool | None) -> None:
        self.task_installed = installed
        if installed is True:
            self.stat_task.configure(text="已安装")
        elif installed is False:
            self.stat_task.configure(text="未安装")
        else:
            self.stat_task.configure(text="未知")

    def load_report(self) -> None:
        for tree in (self.results_tree, self.candidates_tree):
            for item in tree.get_children():
                tree.delete(item)

        try:
            self.report = json.loads(REPORT.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.report = None
        except Exception as exc:
            self.report = None
            self.append_log(f"读取测速报告失败: {exc}\n", "error")

        if not self.report:
            self.empty_label.grid()
            self._load_connectivity_targets()
            return

        selected = self.report.get("selected") or {}
        candidates = self.report.get("candidates") or {}
        probes = self.report.get("probes") or {}

        order = list(self.cfg["domains"])
        for domain in selected:
            if domain not in order:
                order.append(domain)
        for domain in probes:
            if domain not in order:
                order.append(domain)

        for index, domain in enumerate(order):
            selection = selected.get(domain)
            if not selection:
                continue
            median = (
                f"{selection['median_ms']:.1f} ms"
                if selection.get("median_ms") is not None
                else "-"
            )
            ok = (
                f"{selection.get('successes', 0)}/{selection.get('attempts', 0)}"
                if selection.get("attempts")
                else "-"
            )
            kind = {
                "measured": "测速选中",
                "previous": "沿用旧 IP",
                "untested": "未测速兜底",
            }.get(selection.get("kind", ""), selection.get("kind", ""))
            tag = "odd" if index % 2 else "even"
            if selection.get("kind") != "measured":
                tag = "warn"
            self.results_tree.insert(
                "",
                "end",
                values=(
                    domain,
                    selection.get("ip", ""),
                    median,
                    ok,
                    kind,
                    "、".join(selection.get("origins") or []),
                ),
                tags=(tag,),
            )

        if selected:
            self.empty_label.grid_remove()
        else:
            self.empty_label.grid()

        for domain in order:
            origins = {
                item.get("ip"): "、".join(item.get("origins") or [])
                for item in candidates.get(domain, [])
            }
            selected_ip = (selected.get(domain) or {}).get("ip")
            for index, probe in enumerate(probes.get(domain, [])):
                ok = probe.get("ok", False)
                median = (
                    f"{probe['median_ms']:.1f} ms"
                    if probe.get("median_ms") is not None
                    else "-"
                )
                error = " | ".join(probe.get("errors") or [])
                if len(error) > 90:
                    error = error[:87] + "..."
                tags: list[str] = []
                if not ok:
                    tags.append("fail")
                elif probe.get("ip") == selected_ip:
                    tags.append("selected")
                else:
                    tags.append("odd" if index % 2 else "even")
                self.candidates_tree.insert(
                    "",
                    "end",
                    values=(
                        domain,
                        probe.get("ip", ""),
                        "可用" if ok else "失败",
                        median,
                        f"{probe.get('successes', 0)}/{probe.get('attempts', 0)}",
                        origins.get(probe.get("ip"), ""),
                        error,
                    ),
                    tags=tuple(tags),
                )

        self._load_connectivity_targets()

    # ------------------------------------------------------------------
    # 连通性检测
    # ------------------------------------------------------------------

    def _load_connectivity_targets(self) -> None:
        managed_entries: dict[str, str] = {}
        try:
            managed_text = hosts_opt._read_text(
                hosts_opt.default_hosts_path(self.cfg)
            )
            managed_entries = hosts_opt.managed_entries(managed_text)
        except Exception:
            managed_entries = {}

        entries: dict[str, str] = {}
        source = "没有可检测的域名"
        self.conn_from_managed = bool(managed_entries)
        if managed_entries:
            entries = dict(managed_entries)
            source = "系统 hosts 托管块（已生效）"
        elif self.report:
            selected = self.report.get("selected") or {}
            for domain, selection in selected.items():
                ip = selection.get("ip")
                if ip:
                    entries[str(domain)] = str(ip)
            if entries:
                source = "最近预览（尚未写入）"

        order = list(self.cfg["domains"])
        for domain in entries:
            if domain not in order:
                order.append(domain)
        self.conn_targets = {
            domain: entries[domain] for domain in order if domain in entries
        }
        self.conn_source_var.set(
            f"检测对象：{source}（{len(self.conn_targets)} 个域名）"
        )
        self._populate_conn_tree()

    def _populate_conn_tree(self) -> None:
        for item in self.conn_tree.get_children():
            self.conn_tree.delete(item)
        for domain, ip in self.conn_targets.items():
            self.conn_tree.insert(
                "",
                "end",
                values=(domain, ip, "-", "未检测", "-", ""),
                tags=("pending",),
            )
        if self.conn_targets:
            self.conn_summary_var.set("尚未检测")
        else:
            self.conn_summary_var.set("没有可检测的域名")

    def _start_connectivity_check(self) -> None:
        if self.busy or self.checking:
            return
        self._load_connectivity_targets()
        if not self.conn_targets:
            messagebox.showinfo(
                "没有检测对象",
                "当前没有已写入或预览的加速域名。请先拉取并测速，或应用 hosts。",
            )
            return

        self.checking = True
        self.conn_button.configure(state="disabled", text="检测中…")
        self.conn_summary_var.set("正在检测…")
        targets = dict(self.conn_targets)
        self.append_log(
            f"\n=== 连通性检测：{len(targets)} 个域名 ===\n", "cmd"
        )
        threading.Thread(
            target=self._connectivity_worker,
            args=(targets,),
            daemon=True,
        ).start()

    def _connectivity_worker(self, targets: dict[str, str]) -> None:
        cfg = dict(self.cfg)
        cfg["samples"] = max(2, min(3, int(cfg.get("samples", 3))))
        results: dict[str, tuple[str, object]] = {}

        def check(item: tuple[str, str]):
            domain, ip = item
            return domain, ip, hosts_opt.probe_one(domain, ip, cfg)

        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, min(12, len(targets)))
            ) as pool:
                for domain, ip, probe in pool.map(check, targets.items()):
                    results[domain] = (ip, probe)
        except Exception as exc:
            self.queue.put(("conn_error", str(exc)))
            return
        self.queue.put(("conn", (targets, results)))

    def _apply_connectivity_results(self, targets, results) -> None:
        self.checking = False
        self.conn_button.configure(state="normal", text="检测全部")
        for item in self.conn_tree.get_children():
            self.conn_tree.delete(item)

        ok_count = 0
        warn_count = 0
        fail_count = 0
        latencies: list[float] = []
        checked_at = datetime.now().strftime("%H:%M:%S")

        for domain, ip in targets.items():
            entry = results.get(domain)
            probe = entry[1] if entry else None
            error = ""
            if probe is None:
                status, tag, latency, samples = "失败", "fail", "-", "0/0"
                error = "没有返回结果"
                fail_count += 1
            elif not probe.ok:
                status, tag, latency = "失败", "fail", "-"
                samples = f"{probe.successes}/{probe.attempts}"
                error = " | ".join(probe.errors[:1])
                fail_count += 1
            else:
                median = probe.median_ms or 0.0
                latency = f"{median:.0f}"
                samples = f"{probe.successes}/{probe.attempts}"
                latencies.append(median)
                if probe.successes < probe.attempts:
                    status, tag = "不稳定", "warn"
                    warn_count += 1
                else:
                    status, tag = "正常", "ok"
                    ok_count += 1
                if probe.errors:
                    error = " | ".join(probe.errors[:1])

            if len(error) > 90:
                error = error[:87] + "..."
            self.conn_tree.insert(
                "",
                "end",
                values=(domain, ip, latency, status, samples, error),
                tags=(tag,),
            )

        average = sum(latencies) / len(latencies) if latencies else 0.0
        summary = (
            f"共 {len(targets)} 个：正常 {ok_count}，不稳定 {warn_count}，"
            f"失败 {fail_count}，平均 {average:.0f} ms（{checked_at}）"
        )
        self.conn_summary_var.set(summary)
        self.status_var.set(
            f"连通性：正常 {ok_count}，不稳定 {warn_count}，失败 {fail_count}"
        )
        level = (
            "ok"
            if fail_count == 0 and warn_count == 0
            else ("warn" if fail_count == 0 else "error")
        )
        self.append_log(summary + "\n", level)

    def _connectivity_failed(self, message: str) -> None:
        self.checking = False
        self.conn_button.configure(state="normal", text="检测全部")
        self.conn_summary_var.set("检测失败")
        self.append_log(f"连通性检测失败: {message}\n", "error")
        messagebox.showerror("检测失败", message)

    # ------------------------------------------------------------------
    # 备份与回退
    # ------------------------------------------------------------------

    def _load_backups(self) -> None:
        hosts_path = hosts_opt.default_hosts_path(self.cfg)
        if hosts_path.exists():
            try:
                hosts_opt._ensure_original_backup(hosts_path)
            except Exception:
                pass
        try:
            backups = hosts_opt.list_backups(hosts_path)
        except Exception as exc:
            self.backup_summary_var.set(f"读取备份失败: {exc}")
            return

        for item in self.backup_tree.get_children():
            self.backup_tree.delete(item)
        for info in backups:
            kind = "原始" if info.is_original else "自动"
            tags = ("original",) if info.is_original else ()
            self.backup_tree.insert(
                "",
                "end",
                iid=str(info.path),
                values=(
                    info.path.name,
                    info.modified,
                    f"{info.size / 1024:.1f} KB",
                    kind,
                ),
                tags=tags,
            )

        original = hosts_opt.original_backup_path(hosts_path)
        if original.exists():
            mtime = datetime.fromtimestamp(original.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            self.backup_original_var.set(f"原始备份：{original.name}（{mtime}）")
        else:
            self.backup_original_var.set(
                "原始备份：尚未创建（首次备份/应用时自动创建）"
            )
        try:
            keep = int(self.cfg.get("backup_keep", 10))
        except (TypeError, ValueError):
            keep = 10
        self.backup_summary_var.set(
            f"共 {len(backups)} 份备份；原始备份不会被自动清理，"
            f"自动备份保留最近 {keep} 份。"
        )

    def run_backup(self) -> None:
        self._start(
            core_command(["backup"]),
            "备份 hosts",
            callback=self._after_backup,
        )

    def run_rollback_original(self) -> None:
        hosts_path = hosts_opt.default_hosts_path(self.cfg)
        backup = hosts_opt.original_backup_path(hosts_path)
        if not backup.exists():
            messagebox.showinfo(
                "没有原始备份",
                "尚未创建原始备份。请先点击“立即备份”，或执行一次应用。",
            )
            return
        self._confirm_rollback(backup)

    def run_rollback_selected(self) -> None:
        selection = self.backup_tree.selection()
        if not selection:
            messagebox.showinfo("未选择备份", "请先在列表中选择一个备份文件。")
            return
        self._confirm_rollback(Path(selection[0]))

    def _confirm_rollback(self, backup_path: Path) -> None:
        if not messagebox.askyesno(
            "确认回退 hosts",
            f"将用以下备份完整覆盖当前 hosts：\n\n{backup_path}\n\n"
            "当前 hosts 会先自动备份，回退后会自动刷新 DNS。是否继续？",
        ):
            return
        self._start(
            core_command(["rollback", "--backup", str(backup_path)]),
            "回退 hosts",
            requires_admin=True,
            callback=self._after_rollback,
        )

    def _after_backup(self) -> None:
        self._load_backups()
        self.refresh_status()
        self.status_var.set("hosts 备份完成")

    def _after_rollback(self) -> None:
        self.refresh_status()
        self.load_report()
        self._load_backups()
        self.status_var.set("hosts 已回退到备份")

    # ------------------------------------------------------------------
    # 日志与队列
    # ------------------------------------------------------------------

    def _load_log_tail(self) -> None:
        lines: list[str] = []
        try:
            lines = LOG_FILE.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()[-300:]
        except Exception:
            pass

        self.append_log(
            "hosts-opt 图形界面已就绪。\n"
            "1. “拉取并测速”只预览，不修改系统 hosts。\n"
            "2. “应用 hosts”会重新测速并写入，需要管理员权限。\n"
            "3. “安装任务”会按设定间隔在后台自动更新。\n\n",
            "muted",
        )
        if lines:
            self.append_log("—— 最近的运行日志 ——\n", "muted")
            for line in lines:
                self.append_log(line + "\n")

    def append_log(self, text: str, tag: str | None = None) -> None:
        if not hasattr(self, "log_text"):
            return
        line = text.rstrip("\r\n") + "\n"
        if tag is None:
            upper = line.upper()
            if "ERROR" in upper or "失败" in line:
                tag = "error"
            elif "WARNING" in upper or "警告" in line:
                tag = "warn"
            elif "已写入" in line or "已生成" in line or "ONLINE" in line:
                tag = "ok"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line, tag or ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.append_log(payload)
                elif kind == "progress":
                    self._set_progress(*payload)
                elif kind == "done":
                    self._finish_done(*payload)
                elif kind == "failed":
                    self._finish_failed(payload)
                elif kind == "task":
                    self._set_task_status(payload)
                elif kind == "conn":
                    self._apply_connectivity_results(*payload)
                elif kind == "conn_error":
                    self._connectivity_failed(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _set_progress(self, value: int, maximum: int) -> None:
        if self.progress["mode"] != "determinate":
            self.progress.stop()
            self.progress.configure(mode="determinate")
        self.progress.configure(maximum=max(1, maximum), value=value)

    # ------------------------------------------------------------------
    # 执行命令
    # ------------------------------------------------------------------

    def run_update(self) -> None:
        args = ["update"]
        if self.offline_var.get():
            args.append("--offline")
        if self.nodoh_var.get():
            args.append("--no-doh")
        if not self.pools_var.get():
            args.append("--no-pools")
        if self.notion_var.get():
            args.extend(["--enable-pool", "NotionFaster-proxy"])
        self._start(
            core_command(args),
            "拉取并测速",
            callback=self._after_update,
        )

    def run_apply(self) -> None:
        args = ["update", "--apply"]
        if self.offline_var.get():
            args.append("--offline")
        if self.nodoh_var.get():
            args.append("--no-doh")
        if not self.pools_var.get():
            args.append("--no-pools")
        if self.notion_var.get():
            args.extend(["--enable-pool", "NotionFaster-proxy"])
        self._start(
            core_command(args),
            "应用 hosts",
            requires_admin=True,
            callback=self._after_update,
        )

    def run_restore(self) -> None:
        self._start(
            core_command(["restore"]),
            "恢复 hosts",
            requires_admin=True,
            callback=self._after_restore,
        )

    def run_install(self) -> None:
        if not INSTALL_SCRIPT.exists():
            messagebox.showerror(
                "文件缺失",
                f"找不到 {INSTALL_SCRIPT}。\n请保持发布包中的脚本与 EXE 在同一目录。",
            )
            return
        interval = self._read_interval()
        if interval is None:
            return
        self._start(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALL_SCRIPT),
                "-IntervalHours",
                str(interval),
            ],
            "安装定时任务",
            requires_admin=True,
            callback=self._after_task_change,
        )

    def run_uninstall(self) -> None:
        if not UNINSTALL_SCRIPT.exists():
            messagebox.showerror(
                "文件缺失",
                f"找不到 {UNINSTALL_SCRIPT}。\n请保持发布包中的脚本与 EXE 在同一目录。",
            )
            return
        self._start(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(UNINSTALL_SCRIPT),
            ],
            "卸载定时任务",
            requires_admin=True,
            callback=self._after_task_change,
        )

    def _read_interval(self) -> int | None:
        try:
            interval = int(self.interval_var.get())
        except ValueError:
            interval = 0
        if not 1 <= interval <= 24:
            messagebox.showwarning(
                "间隔无效", "定时刷新间隔请填写 1 到 24 之间的小时数。"
            )
            return None
        return interval

    def _start(
        self,
        command: list[str],
        label: str,
        requires_admin: bool = False,
        callback=None,
    ) -> None:
        if self.busy:
            return
        if self.checking:
            messagebox.showinfo(
                "正在检测", "连通性检测正在进行，请稍后再执行其他操作。"
            )
            return
        if requires_admin and not hosts_opt.is_admin():
            if messagebox.askyesno(
                "需要管理员权限",
                "该操作需要管理员权限。\n是否以管理员身份重新启动此界面？",
            ):
                self.elevate()
            return

        self.busy = True
        self._set_busy(True)
        self.status_var.set(f"{label}…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.append_log(f"\n$ {command_text(command)}\n", "cmd")
        threading.Thread(
            target=self._worker,
            args=(command, label, callback),
            daemon=True,
        ).start()

    def _worker(self, command: list[str], label: str, callback) -> None:
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.proc = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
            if self.proc.stdout is None:
                raise RuntimeError("无法读取子进程输出")
            for line in self.proc.stdout:
                self.queue.put(("log", line))
                match = re.search(r"测速进度\s+(\d+)/(\d+)", line)
                if match:
                    self.queue.put(
                        ("progress", (int(match.group(1)), int(match.group(2))))
                    )
            code = self.proc.wait()
            self.queue.put(("done", (code, label, callback)))
        except Exception as exc:
            self.queue.put(("failed", f"{label}: {exc}"))

    def _finish_done(self, code: int, label: str, callback) -> None:
        self.busy = False
        self.proc = None
        self._set_busy(False)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=0)

        if code == 0:
            self.status_var.set(f"{label}完成")
            if callback:
                callback()
            else:
                self.load_report()
                self.refresh_status()
        elif code == 2:
            self.status_var.set(f"{label}需要管理员权限")
            if messagebox.askyesno(
                "需要管理员权限",
                "操作需要管理员权限。\n是否以管理员身份重新启动此界面？",
            ):
                self.elevate()
        elif code == 3:
            self.status_var.set("另一个实例正在运行")
            messagebox.showwarning(
                "正在运行",
                "另一个 hosts-opt 任务正在运行，请稍后再试。",
            )
        else:
            self.status_var.set(f"{label}失败（退出码 {code}）")
            messagebox.showerror(
                "执行失败",
                f"{label}失败，退出码 {code}。\n详情见“运行日志”标签页。",
            )

    def _finish_failed(self, message: str) -> None:
        self.busy = False
        self.proc = None
        self._set_busy(False)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=0)
        self.status_var.set("执行失败")
        self.append_log(message + "\n", "error")
        messagebox.showerror("执行失败", message)

    def _after_update(self) -> None:
        self.load_report()
        self.refresh_status()
        self._load_backups()
        if self.report and self.report.get("apply_error"):
            self.status_var.set(f"应用失败：{self.report['apply_error']}")
        elif self.report and self.report.get("applied"):
            self.status_var.set("hosts 已更新")
            self.root.after(500, self._start_connectivity_check)
        else:
            self.status_var.set("测速完成，可检查结果后应用")

    def _after_restore(self) -> None:
        self.refresh_status()
        self._load_connectivity_targets()
        self._load_backups()
        self.status_var.set("托管块已移除")

    def _after_task_change(self) -> None:
        self._refresh_task_async()
        self.refresh_status()
        self.status_var.set("定时任务状态已更新")
        self.root.after(4000, self.refresh_status)
        self.root.after(10000, self.refresh_status)

    def _sync_pool_controls(self) -> None:
        if self.pools_var.get():
            self.notion_check.configure(state="normal")
        else:
            self.notion_var.set(False)
            self.notion_check.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in self.buttons:
            button.configure(state=state)
            if not busy:
                base_bg = getattr(button, "_base_bg", None)
                if base_bg:
                    button.configure(bg=base_bg)
        for widget in self.option_widgets:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        if not busy:
            self._sync_pool_controls()
        if hasattr(self, "conn_button"):
            if busy:
                self.conn_button.configure(state="disabled")
            elif not self.checking:
                self.conn_button.configure(state="normal", text="检测全部")

    # ------------------------------------------------------------------
    # 其他动作
    # ------------------------------------------------------------------

    def _copy_tree_ip(self, event: tk.Event) -> None:
        tree = event.widget
        item = tree.identify_row(event.y)
        if not item:
            return
        values = tree.item(item, "values")
        if len(values) < 2:
            return
        ip = str(values[1])
        self.root.clipboard_clear()
        self.root.clipboard_append(ip)
        self.status_var.set(f"已复制 {ip}")

    def elevate(self) -> None:
        if hosts_opt.is_admin():
            return
        if getattr(sys, "frozen", False):
            executable = sys.executable
            parameters = ""
        else:
            executable = gui_python()
            parameters = f'"{Path(__file__).resolve()}"'
        try:
            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                executable,
                parameters,
                str(ROOT),
                1,
            )
        except Exception as exc:
            messagebox.showerror("提权失败", str(exc))
            return
        if result <= 32:
            messagebox.showerror(
                "提权失败",
                "未能启动管理员窗口，可能取消了 UAC 授权。",
            )
        else:
            self.root.destroy()

    def _on_close(self) -> None:
        if self.busy:
            if not messagebox.askyesno(
                "任务正在运行",
                "当前任务仍在运行。确定退出并终止它吗？",
            ):
                return
            if self.proc is not None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=3)
                except Exception:
                    pass
                try:
                    locked_pid = int(
                        hosts_opt.LOCK_PATH.read_text(
                            encoding="ascii", errors="ignore"
                        ).strip()
                    )
                    if locked_pid == self.proc.pid:
                        hosts_opt.LOCK_PATH.unlink()
                except Exception:
                    pass
        self.root.destroy()


def main() -> int:
    if "--cli" in sys.argv[1:]:
        cli_args = [arg for arg in sys.argv[1:] if arg != "--cli"]
        if hasattr(sys.stdout, "reconfigure") and not sys.stdout.isatty():
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure") and not sys.stderr.isatty():
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        return hosts_opt.main(cli_args)

    enable_dpi_awareness()
    smoke = "--smoke-test" in sys.argv[1:]
    if not smoke:
        hide_console()
    hosts_opt.ensure_runtime_files()
    root = tk.Tk()
    try:
        HostsOptApp(root)
    except Exception as exc:
        try:
            messagebox.showerror("启动失败", str(exc))
        finally:
            root.destroy()
        return 1

    if smoke:
        root.update_idletasks()
        print("GUI_OK")
        root.destroy()
        return 0

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
