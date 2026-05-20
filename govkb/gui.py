from __future__ import annotations

import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from govkb.core.orchestrator import Orchestrator
from govkb.utils.app_settings import build_runtime_config, load_settings, save_settings, settings_path


class GovKBApp(tk.Tk):
    """Tkinter GUI for GovKB."""

    PROVIDER_DEFAULTS = {
        "none": {"model": "", "base_url": ""},
        "openai": {"model": "gpt-4o-mini", "base_url": "https://api.openai.com/v1"},
        "deepseek": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com/v1"},
        "claude": {"model": "claude-3-5-sonnet-latest", "base_url": "https://api.anthropic.com/v1"},
    }

    def __init__(self):
        super().__init__()
        self.title("GovKB 政府知识库生成器")
        self.geometry("860x660")
        self.minsize(780, 580)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()

        settings = load_settings()
        self.provider = tk.StringVar(value=str(settings["provider"]))
        self.api_key = tk.StringVar(value=str(settings["api_key"]))
        self.model = tk.StringVar(value=str(settings["model"]))
        self.base_url = tk.StringVar(value=str(settings["base_url"]))
        self.output_dir = tk.StringVar(value=str(settings["output_dir"]))
        self.recipe_dir = tk.StringVar(value=str(settings["recipe_dir"]))
        self.target = tk.StringVar(value="青岛市政府")
        self.months = tk.IntVar(value=int(settings["months"]))
        self.max_pages = tk.IntVar(value=int(settings["max_pages"]))
        self.rate_limit = tk.DoubleVar(value=float(settings["rate_limit"]))
        self.concurrency = tk.IntVar(value=int(settings["concurrency"]))
        self.show_trace = tk.BooleanVar(value=bool(settings["show_trace"]))

        self._build()
        self.after(200, self._poll_events)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        settings = ttk.LabelFrame(root, text="模型与输出设置", padding=12)
        settings.pack(fill=tk.X)

        self._row(settings, 0, "供应商")
        provider_box = ttk.Combobox(
            settings,
            textvariable=self.provider,
            values=("none", "openai", "deepseek", "claude"),
            state="readonly",
            width=18,
        )
        provider_box.grid(row=0, column=1, sticky=tk.W, padx=8, pady=4)
        provider_box.bind("<<ComboboxSelected>>", self._on_provider_change)
        ttk.Label(settings, text=f"设置保存于：{settings_path()}").grid(row=0, column=2, columnspan=2, sticky=tk.E, padx=8)

        self._row(settings, 1, "API Key")
        ttk.Entry(settings, textvariable=self.api_key, show="*", width=56).grid(
            row=1, column=1, columnspan=3, sticky=tk.EW, padx=8, pady=4
        )

        self._row(settings, 2, "模型名称")
        ttk.Entry(settings, textvariable=self.model, width=28).grid(row=2, column=1, sticky=tk.EW, padx=8, pady=4)
        self._row(settings, 2, "Base URL", column=2)
        ttk.Entry(settings, textvariable=self.base_url, width=36).grid(row=2, column=3, sticky=tk.EW, padx=8, pady=4)

        self._row(settings, 3, "输出目录")
        ttk.Entry(settings, textvariable=self.output_dir).grid(row=3, column=1, columnspan=2, sticky=tk.EW, padx=8, pady=4)
        ttk.Button(settings, text="选择", command=self._choose_output_dir).grid(row=3, column=3, sticky=tk.W, padx=8, pady=4)

        self._row(settings, 4, "缓存目录")
        ttk.Entry(settings, textvariable=self.recipe_dir).grid(row=4, column=1, columnspan=2, sticky=tk.EW, padx=8, pady=4)
        ttk.Button(settings, text="清理缓存", command=self._clear_cache).grid(row=4, column=3, sticky=tk.W, padx=8, pady=4)
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        task = ttk.LabelFrame(root, text="任务", padding=12)
        task.pack(fill=tk.X, pady=(12, 0))
        self._row(task, 0, "地区或官网")
        ttk.Entry(task, textvariable=self.target).grid(row=0, column=1, columnspan=5, sticky=tk.EW, padx=8, pady=4)

        self._row(task, 1, "最近月数")
        ttk.Spinbox(task, from_=1, to=12, textvariable=self.months, width=8).grid(row=1, column=1, sticky=tk.W, padx=8, pady=4)
        self._row(task, 1, "最大页数", column=2)
        ttk.Spinbox(task, from_=1, to=200, textvariable=self.max_pages, width=8).grid(row=1, column=3, sticky=tk.W, padx=8, pady=4)
        self._row(task, 1, "并发", column=4)
        ttk.Spinbox(task, from_=1, to=10, textvariable=self.concurrency, width=8).grid(row=1, column=5, sticky=tk.W, padx=8, pady=4)
        ttk.Checkbutton(task, text="显示智能体日志", variable=self.show_trace).grid(row=2, column=1, sticky=tk.W, padx=8, pady=4)
        task.columnconfigure(1, weight=1)
        task.columnconfigure(5, weight=1)

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=12)
        self.run_button = ttk.Button(actions, text="开始生成", command=self._start)
        self.run_button.pack(side=tk.LEFT)
        ttk.Button(actions, text="保存设置", command=self._save_settings).pack(side=tk.LEFT, padx=8)
        ttk.Button(actions, text="清空日志", command=self._clear_log).pack(side=tk.LEFT)

        log_frame = ttk.LabelFrame(root, text="日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(log_frame, wrap=tk.WORD, height=18)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.configure(yscrollcommand=scrollbar.set)

    def _row(self, parent: ttk.Frame, row: int, text: str, *, column: int = 0) -> None:
        ttk.Label(parent, text=text).grid(row=row, column=column, sticky=tk.W, padx=4, pady=4)

    def _current_settings(self) -> dict[str, Any]:
        return {
            "provider": self.provider.get(),
            "api_key": self.api_key.get().strip(),
            "model": self.model.get().strip(),
            "base_url": self.base_url.get().strip(),
            "output_dir": self.output_dir.get().strip(),
            "recipe_dir": self.recipe_dir.get().strip(),
            "months": int(self.months.get()),
            "max_pages": int(self.max_pages.get()),
            "rate_limit": float(self.rate_limit.get()),
            "concurrency": int(self.concurrency.get()),
            "show_trace": bool(self.show_trace.get()),
        }

    def _save_settings(self) -> None:
        save_settings(self._current_settings())
        self._append_log(f"设置已保存：{settings_path()}\n")

    def _on_provider_change(self, _event=None) -> None:
        defaults = self.PROVIDER_DEFAULTS.get(self.provider.get(), {})
        self.model.set(defaults.get("model", ""))
        self.base_url.set(defaults.get("base_url", ""))

    def _choose_output_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.output_dir.get() or str(Path.cwd()))
        if path:
            self.output_dir.set(path)

    def _start(self) -> None:
        target = self.target.get().strip()
        if not target:
            messagebox.showwarning("缺少输入", "请输入地区名称或官网 URL。")
            return
        if self.provider.get() != "none" and not self.api_key.get().strip():
            messagebox.showwarning("缺少 API Key", "使用大模型解析地区时需要填写 API Key，也可以选择 none 并输入官网 URL。")
            return

        self._save_settings()
        self.run_button.configure(state=tk.DISABLED)
        self._append_log("开始任务...\n")
        thread = threading.Thread(target=self._run_task, daemon=True)
        thread.start()

    def _run_task(self) -> None:
        try:
            config = build_runtime_config(self._current_settings())
            target = self.target.get().strip()
            self.events.put(("log", f"目标：{target}\n"))
            self.events.put(("log", "正在解析官网、分析新闻栏目、抓取文章并生成文档...\n"))
            result = Orchestrator(config).run(target=target, months=int(self.months.get()), max_pages=int(self.max_pages.get()))
            site = result["site"]
            self.events.put(("log", f"门户：{site.portal_name}\n"))
            self.events.put(("log", f"栏目：{site.news_section_name} - {site.news_list_url}\n"))
            if self.show_trace.get() and result.get("analysis_trace"):
                self.events.put(("log", "智能体日志：\n"))
                for line in result["analysis_trace"]:
                    self.events.put(("log", f"  - {line}\n"))
            self.events.put(("log", f"生成：{result['success']}/{result['article_count']}\n"))
            self.events.put(("log", f"输出目录：{result['output_dir']}\n"))
            self.events.put(("done", result))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_log(payload)
            elif kind == "done":
                self.run_button.configure(state=tk.NORMAL)
                messagebox.showinfo("完成", f"生成完成：{payload['success']}/{payload['article_count']}\n{payload['output_dir']}")
            elif kind == "error":
                self.run_button.configure(state=tk.NORMAL)
                self._append_log(f"错误：{payload}\n")
                messagebox.showerror("任务失败", payload)
        self.after(200, self._poll_events)

    def _append_log(self, text: str) -> None:
        self.log.insert(tk.END, text)
        self.log.see(tk.END)

    def _clear_log(self) -> None:
        self.log.delete("1.0", tk.END)

    def _clear_cache(self) -> None:
        path = Path(self.recipe_dir.get().strip() or "recipes")
        if not path.exists():
            self._append_log(f"缓存目录不存在：{path}\n")
            return
        if not messagebox.askyesno("清理缓存", f"确定删除缓存目录？\n{path}"):
            return
        try:
            shutil.rmtree(path)
            self._append_log(f"已清理缓存：{path}\n")
        except Exception as exc:
            messagebox.showerror("清理失败", str(exc))


def main() -> int:
    app = GovKBApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
