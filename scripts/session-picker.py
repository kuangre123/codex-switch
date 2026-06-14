#!/usr/bin/env python3
"""Checkbox session picker used by the macOS app."""

from __future__ import annotations

import re
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox


def default_cli() -> str:
    home_cli = Path.home() / ".local" / "bin" / "codex-switch"
    if home_cli.exists():
        return str(home_cli)
    here = Path(__file__).resolve()
    bundled = here.parents[1] / "src" / "codex_switch" / "cli.py"
    if bundled.exists():
        return f"/usr/bin/env python3 {bundled}"
    return "codex-switch"


def run_cli(cli: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = [cli, *args]
    if " " in cli:
        command = ["/bin/sh", "-lc", " ".join([cli, *[shell_quote(arg) for arg in args]])]
    return subprocess.run(command, text=True, capture_output=True, check=False)


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def session_id_from_choice(choice: str) -> str:
    match = re.search(r"#([^#\s]+)$", choice.strip())
    return match.group(1) if match else ""


def main() -> int:
    cli = sys.argv[1] if len(sys.argv) > 1 else default_cli()
    result = run_cli(cli, "sessions", "recent", "--limit", "10")
    if result.returncode != 0:
        messagebox.showerror("Codex Switch 会话", result.stderr.strip() or result.stdout.strip() or "读取最近会话失败")
        return result.returncode

    choices = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not choices:
        messagebox.showinfo("Codex Switch 会话", "没有找到最近对话。可以先在会话工具里重建会话索引。")
        return 0

    root = tk.Tk()
    root.title("Codex Switch 会话")
    root.resizable(False, False)

    selected: list[str] = []
    variables: list[tuple[tk.BooleanVar, str]] = []

    header = tk.Label(root, text="选择要继续的对话（可多选）", anchor="w", font=("Helvetica", 14, "bold"))
    header.pack(fill="x", padx=18, pady=(16, 8))

    hint = tk.Label(root, text="选中的对话会排到 Codex 最近会话列表，方便刷新后继续打开。", anchor="w", fg="#555555")
    hint.pack(fill="x", padx=18, pady=(0, 10))

    frame = tk.Frame(root)
    frame.pack(fill="both", padx=18)

    for choice in choices:
        var = tk.BooleanVar(value=False)
        variables.append((var, choice))
        item = tk.Checkbutton(
            frame,
            text=choice,
            variable=var,
            anchor="w",
            justify="left",
            wraplength=780,
            padx=4,
            pady=3,
        )
        item.pack(fill="x", anchor="w")

    button_frame = tk.Frame(root)
    button_frame.pack(fill="x", padx=18, pady=18)

    def cancel() -> None:
        root.destroy()

    def submit() -> None:
        for var, choice in variables:
            if var.get():
                session_id = session_id_from_choice(choice)
                if session_id:
                    selected.append(session_id)
        root.destroy()

    cancel_button = tk.Button(button_frame, text="取消", command=cancel, width=10)
    cancel_button.pack(side="right", padx=(8, 0))
    submit_button = tk.Button(button_frame, text="置为最近", command=submit, width=12, default="active")
    submit_button.pack(side="right")

    root.bind("<Escape>", lambda _: cancel())
    root.bind("<Return>", lambda _: submit())
    root.update_idletasks()
    width = min(max(root.winfo_width(), 760), 940)
    height = root.winfo_height()
    x = root.winfo_screenwidth() // 2 - width // 2
    y = root.winfo_screenheight() // 2 - height // 2
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.mainloop()

    for session_id in selected:
        print(session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

