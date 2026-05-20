from __future__ import annotations

import argparse
import json
import threading
import uuid
import webbrowser
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from govkb.core.orchestrator import Orchestrator
from govkb.utils.app_settings import build_runtime_config, load_settings, save_settings, settings_path


TASKS: dict[str, dict[str, Any]] = {}
TASK_LOCK = threading.Lock()


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>GovKB 智能采集台</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #172033;
      --muted: #667085;
      --line: #d8dee9;
      --panel: #ffffff;
      --bg: #eef3f8;
      --brand: #0f766e;
      --brand-dark: #115e59;
      --accent: #b45309;
      --danger: #b42318;
      --shadow: 0 18px 42px rgba(16, 24, 40, .12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
      color: var(--ink);
      background:
        linear-gradient(180deg, rgba(15,118,110,.12), transparent 260px),
        var(--bg);
    }
    header {
      padding: 28px clamp(18px, 4vw, 48px) 18px;
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
    }
    h1 { margin: 0; font-size: clamp(26px, 4vw, 42px); letter-spacing: 0; }
    .sub { margin: 8px 0 0; color: var(--muted); max-width: 720px; line-height: 1.7; }
    .badge {
      padding: 8px 12px;
      border: 1px solid rgba(15,118,110,.24);
      border-radius: 999px;
      color: var(--brand-dark);
      background: rgba(255,255,255,.72);
      white-space: nowrap;
    }
    main {
      padding: 12px clamp(18px, 4vw, 48px) 42px;
      display: grid;
      grid-template-columns: minmax(320px, 520px) minmax(320px, 1fr);
      gap: 18px;
    }
    section {
      background: var(--panel);
      border: 1px solid rgba(23,32,51,.08);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .section-title {
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      font-weight: 700;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }
    .form { padding: 18px; display: grid; gap: 14px; }
    label { display: grid; gap: 7px; color: #344054; font-size: 14px; }
    input, select {
      width: 100%;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      padding: 11px 12px;
      font: inherit;
      background: #fff;
      color: var(--ink);
    }
    input:focus, select:focus { outline: 2px solid rgba(15,118,110,.22); border-color: var(--brand); }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; padding-top: 4px; }
    button {
      border: 0;
      border-radius: 6px;
      padding: 11px 15px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    .primary { color: #fff; background: var(--brand); }
    .primary:hover { background: var(--brand-dark); }
    .ghost { color: var(--brand-dark); background: #e6f4f1; }
    .danger { color: #fff; background: var(--danger); }
    .status {
      padding: 18px;
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      border-bottom: 1px solid var(--line);
    }
    .metric { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; min-height: 76px; }
    .metric b { display: block; font-size: 22px; margin-top: 7px; }
    .log {
      margin: 0;
      padding: 18px;
      height: 470px;
      overflow: auto;
      background: #111827;
      color: #d1fae5;
      font: 13px/1.65 Consolas, "Courier New", monospace;
      white-space: pre-wrap;
    }
    .hint { color: var(--muted); font-size: 13px; line-height: 1.6; }
    a { color: var(--brand-dark); }
    @media (max-width: 920px) {
      header { display: block; }
      .badge { display: inline-block; margin-top: 14px; }
      main { grid-template-columns: 1fr; }
      .status, .grid-3, .grid-2 { grid-template-columns: 1fr; }
      .log { height: 360px; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>GovKB 智能采集台</h1>
      <p class="sub">输入地区或政府官网，自动解析官方站点、定位新闻栏目、翻页抓取近期文章并生成 Word 文档。</p>
    </div>
    <div class="badge" id="settingsPath">读取设置中...</div>
  </header>
  <main>
    <section>
      <div class="section-title">任务配置 <span class="hint">本地保存，便于下次继续</span></div>
      <div class="form">
        <label>地区或官网 URL
          <input id="target" placeholder="例如：西宁市政府 / https://www.xining.gov.cn" value="青岛市政府" />
        </label>
        <div class="grid-2">
          <label>供应商
            <select id="provider">
              <option value="none">none</option>
              <option value="openai">openai</option>
              <option value="deepseek">deepseek</option>
              <option value="claude">claude</option>
            </select>
          </label>
          <label>模型名称
            <input id="model" />
          </label>
        </div>
        <label>API Key
          <input id="api_key" type="password" autocomplete="off" />
        </label>
        <label>Base URL
          <input id="base_url" />
        </label>
        <label>输出目录
          <input id="output_dir" />
        </label>
        <label>缓存目录
          <input id="recipe_dir" />
        </label>
        <div class="grid-3">
          <label>最近月数 <input id="months" type="number" min="1" max="12" /></label>
          <label>最大页数 <input id="max_pages" type="number" min="1" max="200" /></label>
          <label>并发数 <input id="concurrency" type="number" min="1" max="10" /></label>
        </div>
        <div class="actions">
          <button class="primary" id="runBtn">开始生成</button>
          <button class="ghost" id="saveBtn">保存设置</button>
          <button class="danger" id="clearBtn">清理缓存</button>
        </div>
        <div class="hint">API Key 会保存在当前用户目录的 GovKB 设置文件中，请只在可信电脑上使用。</div>
      </div>
    </section>
    <section>
      <div class="section-title">运行状态 <span class="hint" id="taskState">空闲</span></div>
      <div class="status">
        <div class="metric">门户<b id="portal">-</b></div>
        <div class="metric">栏目<b id="section">-</b></div>
        <div class="metric">文章<b id="count">0</b></div>
        <div class="metric">生成<b id="success">0</b></div>
      </div>
      <pre class="log" id="log">准备就绪。</pre>
    </section>
  </main>
  <script>
    const defaults = {
      none: {model: "", base_url: ""},
      openai: {model: "gpt-4o-mini", base_url: "https://api.openai.com/v1"},
      deepseek: {model: "deepseek-v4-flash", base_url: "https://api.deepseek.com/v1"},
      claude: {model: "claude-3-5-sonnet-latest", base_url: "https://api.anthropic.com/v1"}
    };
    const ids = ["provider","api_key","model","base_url","output_dir","recipe_dir","months","max_pages","concurrency"];
    const $ = id => document.getElementById(id);
    let pollTimer = null;

    function collect() {
      const data = {};
      ids.forEach(id => data[id] = $(id).value);
      data.months = Number(data.months || 3);
      data.max_pages = Number(data.max_pages || 30);
      data.concurrency = Number(data.concurrency || 3);
      data.target = $("target").value.trim();
      return data;
    }
    function applySettings(data) {
      ids.forEach(id => { if (data[id] !== undefined) $(id).value = data[id]; });
      $("settingsPath").textContent = data.settings_path || "本地设置";
    }
    function log(text) { $("log").textContent = text; $("log").scrollTop = $("log").scrollHeight; }
    async function load() {
      const res = await fetch("/api/settings");
      applySettings(await res.json());
    }
    async function save() {
      const res = await fetch("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(collect())});
      applySettings(await res.json());
      log("设置已保存。");
    }
    async function run() {
      const data = collect();
      if (!data.target) { alert("请输入地区或官网 URL"); return; }
      if (data.provider !== "none" && !data.api_key) { alert("请填写 API Key，或选择 none 并输入官网 URL"); return; }
      await save();
      $("runBtn").disabled = true;
      log("开始任务...\n目标：" + data.target + "\n");
      const res = await fetch("/api/run", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
      const task = await res.json();
      pollTimer = setInterval(() => poll(task.id), 1200);
    }
    async function poll(id) {
      const res = await fetch("/api/tasks/" + id);
      const task = await res.json();
      $("taskState").textContent = task.status;
      log((task.log || []).join("\n"));
      if (task.result) {
        const site = task.result.site || {};
        $("portal").textContent = site.portal_name || "-";
        $("section").textContent = site.news_section_name || "-";
        $("count").textContent = task.result.article_count || 0;
        $("success").textContent = task.result.success || 0;
      }
      if (task.status === "done" || task.status === "error") {
        clearInterval(pollTimer);
        $("runBtn").disabled = false;
      }
    }
    async function clearCache() {
      if (!confirm("确定清理缓存目录？")) return;
      const res = await fetch("/api/cache", {method: "DELETE", headers: {"Content-Type": "application/json"}, body: JSON.stringify(collect())});
      const data = await res.json();
      log(data.message || "缓存已处理。");
    }
    $("provider").addEventListener("change", () => {
      const preset = defaults[$("provider").value] || defaults.none;
      $("model").value = preset.model;
      $("base_url").value = preset.base_url;
    });
    $("saveBtn").addEventListener("click", save);
    $("runBtn").addEventListener("click", run);
    $("clearBtn").addEventListener("click", clearCache);
    load();
  </script>
</body>
</html>
"""


class WebUIHandler(BaseHTTPRequestHandler):
    server_version = "GovKBWebUI/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(INDEX_HTML)
            return
        if parsed.path == "/api/settings":
            settings = load_settings()
            settings["settings_path"] = str(settings_path())
            self._send_json(settings)
            return
        if parsed.path.startswith("/api/tasks/"):
            task_id = parsed.path.rsplit("/", 1)[-1]
            with TASK_LOCK:
                task = TASKS.get(task_id)
            self._send_json(task or {"status": "missing", "log": ["任务不存在"]}, status=HTTPStatus.OK if task else HTTPStatus.NOT_FOUND)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        data = self._read_json()
        if parsed.path == "/api/settings":
            save_settings(data)
            settings = load_settings()
            settings["settings_path"] = str(settings_path())
            self._send_json(settings)
            return
        if parsed.path == "/api/run":
            task_id = uuid.uuid4().hex
            task = {"id": task_id, "status": "running", "log": ["开始任务...", f"目标：{data.get('target', '')}"]}
            with TASK_LOCK:
                TASKS[task_id] = task
            threading.Thread(target=_run_task, args=(task_id, data), daemon=True).start()
            self._send_json({"id": task_id})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        data = self._read_json()
        if parsed.path == "/api/cache":
            from pathlib import Path
            import shutil

            path = Path(str(data.get("recipe_dir") or load_settings().get("recipe_dir") or "recipes"))
            try:
                if path.exists():
                    shutil.rmtree(path)
                    self._send_json({"message": f"已清理缓存：{path}"})
                else:
                    self._send_json({"message": f"缓存目录不存在：{path}"})
            except Exception as exc:
                self._send_json({"message": f"清理失败：{exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(_jsonable(data), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _run_task(task_id: str, data: dict[str, Any]) -> None:
    def update(**changes: Any) -> None:
        with TASK_LOCK:
            TASKS[task_id].update(changes)

    def append(line: str) -> None:
        with TASK_LOCK:
            TASKS[task_id].setdefault("log", []).append(line)

    try:
        settings = load_settings()
        settings.update(data)
        config = build_runtime_config(settings)
        target = str(data.get("target") or "").strip()
        append("正在解析官网、分析新闻栏目、抓取文章并生成文档...")
        result = Orchestrator(config).run(target=target, months=int(settings.get("months") or 3), max_pages=int(settings.get("max_pages") or 30))
        site = result["site"]
        append(f"门户：{site.portal_name}")
        append(f"栏目：{site.news_section_name} - {site.news_list_url}")
        for line in result.get("analysis_trace") or []:
            append(f"智能体：{line}")
        append(f"生成：{result['success']}/{result['article_count']}")
        append(f"输出目录：{result['output_dir']}")
        update(status="done", result=result)
    except Exception as exc:
        append(f"错误：{exc}")
        update(status="error", error=str(exc))


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def serve(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), WebUIHandler)
    url = f"http://{host}:{port}/"
    print(f"GovKB WebUI: {url}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="govkb-webui", description="Start the GovKB local WebUI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    args = parser.parse_args(argv)
    serve(args.host, args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
