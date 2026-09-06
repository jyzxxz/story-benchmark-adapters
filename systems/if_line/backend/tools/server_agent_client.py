#!/usr/bin/env python3
"""服务端 Agent 假前端控制台。

使用形态接近 shell：
- 普通输入直接作为用户 prompt 发给 Agent；
- 正常完成后不回显 Agent 输出，只回到 `>`；
- 详细输出、toolcall、token、瀑布图去 Phoenix 看；
- Agent 请求前端处理 VNGraph 时，本脚本用 `/loadvn` 加载的 JSON 模拟前端工作区；
- VNGraph patch 和校验调用 IfLine/tools/VNGraphFrontendTool 的 C# 实现。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent

EEvent_item_updated = "item.updated"
EEvent_turn_completed = "turn.completed"
EEvent_turn_failed = "turn.failed"
EItem_tool_call = "tool_call"
EItemStatus_waiting_input = "waiting_input"


@dataclass
class Config:
    base_url: str
    email: str
    password: str
    guest: bool
    agent_kind: str
    mode: str
    thread_id: str
    resume_latest: bool
    resume_status: str
    resume_agent_kind: str
    resume_limit: int
    timeout_seconds: float
    poll_interval_seconds: float
    project_id: str
    chapter_index: str
    vn_graph_revision_id: str
    node_index: str
    vn_graph_file: str
    vn_graph_tool_project: str
    save_vngraph_on_patch: bool
    trace_events: bool


class ApiClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            urllib.request.ProxyHandler({}),
        )

    def request_json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url + path
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {raw}") from exc
        if not text.strip():
            return {}
        return json.loads(text)


class VNGraphWorkspace:
    def __init__(self, tool_project: Path):
        self.tool_project = tool_project
        self.path: Path | None = None
        self.graph: dict[str, Any] | None = None
        self.project_id = ""
        self.chapter_index = ""
        self.revision_id = ""
        self.node_index = ""

    def load(self, path: Path, revision_id: str = "") -> None:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict) and isinstance(payload.get("graph_json"), dict):
            self.graph = payload["graph_json"]
            self.revision_id = revision_id or str(payload.get("id") or payload.get("revision_id") or "")
        elif isinstance(payload, dict):
            self.graph = payload
            self.revision_id = revision_id or self.revision_id
        else:
            raise ValueError("VNGraph 文件必须是 JSON 对象，或包含 graph_json 字段")
        self.path = path
        self.validate()

    def save(self, path: Path | None = None) -> None:
        if self.graph is None:
            raise ValueError("尚未加载 VNGraph")
        target = path or self.path
        if target is None:
            raise ValueError("没有保存路径，请用 /savevn <json文件>")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def validate(self) -> dict[str, Any]:
        if self.graph is None:
            raise ValueError("尚未加载 VNGraph")
        result = apply_patch_with_csharp_frontend(self.graph, [], self.tool_project)
        if not result.get("ok"):
            raise RuntimeError("C# VNGraph 校验失败: " + json.dumps(result, ensure_ascii=False))
        return result

    def apply_patch(self, patch: list[dict[str, Any]]) -> dict[str, Any]:
        if self.graph is None:
            return {"ok": False, "error": "尚未加载 VNGraph，请先执行 /loadvn <json文件>"}
        result = apply_patch_with_csharp_frontend(self.graph, patch, self.tool_project)
        if result.get("ok") and isinstance(result.get("graph_json"), dict):
            self.graph = result["graph_json"]
        return result

    def build_hidden_prompt(self) -> str:
        if self.graph is None:
            return ""
        parts = [
            "前端上下文：当前假前端已经加载 VNGraph 草稿，可以处理 VNGraph 草稿读取和 patch 预览。",
        ]
        if self.project_id:
            parts.append(f"- project_id={self.project_id}")
        if self.chapter_index:
            parts.append(f"- chapter_index={self.chapter_index}")
        if self.revision_id:
            parts.append(f"- 当前 VNGraph revision id={self.revision_id}")
        if self.node_index:
            parts.append(f"- 当前选中节点 node_index={self.node_index}")
        parts.extend(
            [
                "如果需要读取当前本地草稿，请调用 request_frontend_vngraph_draft。",
                "如果需要修改 VNGraph，请调用 apply_vngraph_patch；target 优先使用 vn_graph_revision_id 和 node_index。",
                "apply_vngraph_patch 后必须调用 get_frontend_tool_result 读取前端处理结果；不要让用户手填技术 ID。",
            ]
        )
        return "\n".join(parts)


class AgentConsole:
    def __init__(self, config: Config):
        self.config = config
        self.client = ApiClient(config.base_url, timeout=30.0)
        self.workspace = VNGraphWorkspace(resolve_vngraph_tool_project(config.vn_graph_tool_project))
        self.thread_id = config.thread_id
        self.after_seq = 0
        self.handled_tool_calls: set[str] = set()

        self.workspace.project_id = config.project_id
        self.workspace.chapter_index = config.chapter_index
        self.workspace.revision_id = config.vn_graph_revision_id
        self.workspace.node_index = config.node_index

    def start(self) -> None:
        self.login()
        if self.config.vn_graph_file:
            self.workspace.load(Path(self.config.vn_graph_file), self.config.vn_graph_revision_id)
        if self.config.resume_latest:
            self.thread_id = self.select_latest_thread(
                status=self.config.resume_status,
                agent_kind=self.config.resume_agent_kind or self.config.agent_kind,
                limit=self.config.resume_limit,
            )
        if not self.thread_id:
            self.thread_id = self.create_thread()
        self.print_thread_hint("ready")

    def login(self) -> None:
        if self.config.guest:
            self.client.request_json("POST", "/api/auth/guest", {})
            return
        if not self.config.email or not self.config.password:
            raise RuntimeError("需要配置 IFLINE_AGENT_EMAIL/IFLINE_AGENT_PASSWORD，或设置 IFLINE_AGENT_GUEST=true")
        self.client.request_json(
            "POST",
            "/api/auth/login",
            {"email": self.config.email, "password": self.config.password},
        )

    def create_thread(self) -> str:
        body: dict[str, Any] = {"agent_kind": self.config.agent_kind}
        if self.config.mode:
            body["mode"] = self.config.mode
        snapshot = self.client.request_json("POST", "/api/server-agent/threads", body)
        thread_id = str(snapshot.get("thread_id") or "")
        if not thread_id:
            raise RuntimeError("创建 Agent thread 失败: " + json.dumps(snapshot, ensure_ascii=False))
        return thread_id

    def run_shell(self) -> None:
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                print()
                self.print_thread_hint("exit")
                return
            if not line:
                continue
            try:
                if line.startswith("/"):
                    if self.run_command(line):
                        self.print_thread_hint("exit")
                        return
                    continue
                self.submit_user_input(line)
            except KeyboardInterrupt:
                print("\n已打断本地等待；后端 turn 不会因为本地 Ctrl-C 自动取消。", file=sys.stderr)
            except Exception as exc:
                print("错误: " + str(exc), file=sys.stderr)

    def run_command(self, line: str) -> bool:
        args = shlex.split(line)
        command = args[0]
        if command in {"/q", "/quit", "/exit"}:
            return True
        if command == "/help":
            self.print_help()
            return False
        if command == "/new":
            self.thread_id = self.create_thread()
            self.after_seq = 0
            self.handled_tool_calls.clear()
            self.print_thread_hint("new")
            return False
        if command == "/thread":
            if len(args) != 2:
                raise ValueError("用法: /thread <thread_id>")
            self.switch_thread(args[1])
            return False
        if command == "/resume":
            self.command_resume(args[1:])
            return False
        if command == "/loadvn":
            if len(args) < 2:
                raise ValueError("用法: /loadvn <json文件> [revision_id]")
            self.workspace.load(Path(args[1]), args[2] if len(args) >= 3 else self.workspace.revision_id)
            return False
        if command == "/savevn":
            self.workspace.save(Path(args[1]) if len(args) >= 2 else None)
            return False
        if command == "/project":
            if len(args) != 2:
                raise ValueError("用法: /project <project_id>")
            self.workspace.project_id = args[1]
            return False
        if command == "/chapter":
            if len(args) != 2:
                raise ValueError("用法: /chapter <chapter_index>")
            self.workspace.chapter_index = args[1]
            return False
        if command == "/revision":
            if len(args) != 2:
                raise ValueError("用法: /revision <vn_graph_revision_id>")
            self.workspace.revision_id = args[1]
            return False
        if command == "/node":
            if len(args) != 2:
                raise ValueError("用法: /node <node_index>")
            self.workspace.node_index = args[1]
            return False
        if command == "/validatevn":
            self.workspace.validate()
            return False
        if command == "/status":
            self.print_status()
            return False
        raise ValueError("未知命令: " + command)

    def command_resume(self, args: list[str]) -> None:
        if args and args[0] == "list":
            filters = parse_key_args(args[1:])
            items = self.list_threads(
                status=filters.get("status", self.config.resume_status),
                agent_kind=filters.get("agent_kind", self.config.resume_agent_kind or self.config.agent_kind),
                limit=parse_int(filters.get("limit", ""), self.config.resume_limit),
            )
            print_thread_list(items)
            return
        if args and "=" not in args[0]:
            self.switch_thread(args[0])
            return
        filters = parse_key_args(args)
        thread_id = self.select_latest_thread(
            status=filters.get("status", self.config.resume_status),
            agent_kind=filters.get("agent_kind", self.config.resume_agent_kind or self.config.agent_kind),
            limit=parse_int(filters.get("limit", ""), self.config.resume_limit),
        )
        self.switch_thread(thread_id)

    def list_threads(self, *, status: str, agent_kind: str, limit: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 200)), "offset": 0}
        if status:
            params["status"] = status
        if agent_kind:
            params["agent_kind"] = agent_kind
        query = urllib.parse.urlencode(params)
        page = self.client.request_json("GET", f"/api/server-agent/threads?{query}")
        items = page.get("items") if isinstance(page.get("items"), list) else []
        return [item for item in items if isinstance(item, dict)]

    def select_latest_thread(self, *, status: str, agent_kind: str, limit: int) -> str:
        items = self.list_threads(status=status, agent_kind=agent_kind, limit=limit)
        if not items:
            raise RuntimeError("没有找到可 resume 的 Agent thread")
        return str(items[0].get("thread_id") or "")

    def switch_thread(self, thread_id: str) -> None:
        if not thread_id:
            raise ValueError("thread_id 不能为空")
        self.thread_id = thread_id
        self.after_seq = 0
        self.handled_tool_calls.clear()
        self.print_thread_hint("resume")

    def submit_user_input(self, content: str) -> None:
        body = {
            "content": content,
            "prompt": self.workspace.build_hidden_prompt(),
        }
        if self.config.mode:
            body["mode"] = self.config.mode
        accepted = self.client.request_json(
            "POST",
            f"/api/server-agent/threads/{quote_path(self.thread_id)}/turns/resume",
            body,
        )
        turn_id = str(accepted.get("turn_id") or "")
        if not turn_id:
            raise RuntimeError("提交 turn 失败: " + json.dumps(accepted, ensure_ascii=False))
        self.wait_turn(turn_id)
        self.print_thread_hint("turn_done")

    def wait_turn(self, turn_id: str) -> None:
        deadline = time.monotonic() + self.config.timeout_seconds
        while time.monotonic() < deadline:
            page = self.fetch_events()
            events = page.get("events") if isinstance(page.get("events"), list) else []
            for event in events:
                self.after_seq = max(self.after_seq, int(event.get("seq") or self.after_seq))
                if self.config.trace_events:
                    print(json.dumps(event, ensure_ascii=False), file=sys.stderr)
                self.handle_event(event)
                if event.get("turn_id") == turn_id and event.get("type") == EEvent_turn_completed:
                    return
                if event.get("turn_id") == turn_id and event.get("type") == EEvent_turn_failed:
                    raise RuntimeError("Agent turn 失败，详情请看 Phoenix 或事件日志")
            time.sleep(self.config.poll_interval_seconds)
        raise TimeoutError("等待 Agent turn 完成超时")

    def fetch_events(self) -> dict[str, Any]:
        query = urllib.parse.urlencode({"after_seq": self.after_seq, "limit": 200})
        return self.client.request_json(
            "GET",
            f"/api/server-agent/threads/{quote_path(self.thread_id)}/events/page?{query}",
        )

    def handle_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != EEvent_item_updated:
            return
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if item.get("type") != EItem_tool_call or item.get("status") != EItemStatus_waiting_input:
            return
        tool_call_id = str(item.get("id") or "")
        if not tool_call_id or tool_call_id in self.handled_tool_calls:
            return
        self.handled_tool_calls.add(tool_call_id)
        input_request = item.get("input_request") if isinstance(item.get("input_request"), dict) else {}
        result = self.handle_frontend_tool(input_request)
        self.client.request_json(
            "POST",
            f"/api/server-agent/threads/{quote_path(self.thread_id)}/tool-calls/{quote_path(tool_call_id)}/result",
            {"result": result},
        )

    def handle_frontend_tool(self, request: dict[str, Any]) -> dict[str, Any]:
        request_type = str(request.get("type") or "")
        if request_type == "vngraph_draft.requested":
            if self.workspace.graph is None:
                return {"ok": False, "error": "前端工作区尚未加载 VNGraph"}
            return {
                "ok": True,
                "graph_json": self.workspace.graph,
                "result_graph_hash": hash_json(self.workspace.graph),
                "project_id": self.workspace.project_id,
                "chapter_index": self.workspace.chapter_index,
                "vn_graph_revision_id": self.workspace.revision_id,
            }
        if request_type == "vngraph_apply.requested":
            patch = request.get("vngraph_patch")
            if not isinstance(patch, list):
                return {"ok": False, "error": "前端 apply 请求缺少 vngraph_patch 数组"}
            result = self.workspace.apply_patch(patch)
            if result.get("ok") and self.config.save_vngraph_on_patch:
                self.workspace.save()
            if result.get("ok"):
                return {
                    "ok": True,
                    "action_id": request.get("action_id"),
                    "graph_json": result.get("graph_json"),
                    "result_graph_hash": result.get("result_graph_hash"),
                    "summary": result.get("summary"),
                }
            return {
                "ok": False,
                "action_id": request.get("action_id"),
                "error": result.get("error") or "C# 前端应用 VNGraph patch 失败",
                "detail": result,
            }
        return {"ok": False, "error": "未知前端工具请求: " + request_type, "request": request}

    def print_help(self) -> None:
        print(
            "\n".join(
                [
                    "普通输入: 直接发送给 Agent；成功后只回到 > 提示符。",
                    "/resume                       切换到最近一个匹配的 thread。",
                    "/resume list [key=value...]    列出最近 thread。支持 status/agent_kind/limit。",
                    "/resume <thread_id>            切换到指定 thread。",
                    "/loadvn <json文件> [revision]  加载本地 VNGraph 草稿并用 C# 校验。",
                    "/savevn [json文件]             保存当前本地 VNGraph 草稿。",
                    "/project <project_id>          设置前端上下文项目 ID。",
                    "/chapter <chapter_index>       设置前端上下文章节号。",
                    "/revision <revision_id>        设置当前 VNGraph revision id。",
                    "/node <node_index>             设置当前选中节点。",
                    "/validatevn                    用 C# 校验当前 VNGraph。",
                    "/new                           创建新的 Agent thread。",
                    "/thread <thread_id>            切换到已有 Agent thread。",
                    "/status                        打印当前控制台状态。",
                    "/quit                          退出。",
                ]
            )
        )

    def print_status(self) -> None:
        payload = {
            "base_url": self.config.base_url,
            "thread_id": self.thread_id,
            "project_id": self.workspace.project_id,
            "chapter_index": self.workspace.chapter_index,
            "vn_graph_revision_id": self.workspace.revision_id,
            "node_index": self.workspace.node_index,
            "vn_graph_file": str(self.workspace.path) if self.workspace.path else "",
            "graph_loaded": self.workspace.graph is not None,
            "after_seq": self.after_seq,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    def print_thread_hint(self, label: str) -> None:
        print(f"[{label}] thread_id={self.thread_id}", file=sys.stderr)


def load_config(args: argparse.Namespace) -> Config:
    file_values = read_env_file(Path(args.env_file)) if args.env_file else {}

    def get(name: str, default: str = "") -> str:
        return str(os.environ.get(name) or file_values.get(name) or default)

    def pick(cli_value: Any, env_name: str, default: str = "") -> str:
        if cli_value is not None:
            return str(cli_value)
        return get(env_name, default)

    return Config(
        base_url=pick(args.base_url, "IFLINE_AGENT_BASE_URL", "http://127.0.0.1:60002"),
        email=pick(args.email, "IFLINE_AGENT_EMAIL"),
        password=pick(args.password, "IFLINE_AGENT_PASSWORD"),
        guest=args.guest or parse_bool(get("IFLINE_AGENT_GUEST")),
        agent_kind=pick(args.agent_kind, "IFLINE_AGENT_KIND", "server_agent"),
        mode=pick(args.mode, "IFLINE_AGENT_MODE"),
        thread_id=pick(args.thread_id, "IFLINE_AGENT_THREAD_ID"),
        resume_latest=args.resume or parse_bool(get("IFLINE_AGENT_RESUME")),
        resume_status=pick(args.resume_status, "IFLINE_AGENT_RESUME_STATUS"),
        resume_agent_kind=pick(args.resume_agent_kind, "IFLINE_AGENT_RESUME_AGENT_KIND"),
        resume_limit=parse_int(pick(args.resume_limit, "IFLINE_AGENT_RESUME_LIMIT", "20"), 20),
        timeout_seconds=parse_float(pick(args.timeout, "IFLINE_AGENT_TIMEOUT_SECONDS", "180"), 180.0),
        poll_interval_seconds=parse_float(pick(args.poll_interval, "IFLINE_AGENT_POLL_INTERVAL_SECONDS", "0.4"), 0.4),
        project_id=pick(args.project_id, "IFLINE_AGENT_PROJECT_ID"),
        chapter_index=pick(args.chapter_index, "IFLINE_AGENT_CHAPTER_INDEX"),
        vn_graph_revision_id=pick(args.vn_graph_revision_id, "IFLINE_AGENT_VNGRAPH_REVISION_ID"),
        node_index=pick(args.node_index, "IFLINE_AGENT_NODE_INDEX"),
        vn_graph_file=pick(args.vn_graph_file, "IFLINE_AGENT_VNGRAPH_FILE"),
        vn_graph_tool_project=pick(args.vn_graph_tool_project, "IFLINE_AGENT_VNGRAPH_TOOL_PROJECT"),
        save_vngraph_on_patch=args.save_vngraph_on_patch or parse_bool(get("IFLINE_AGENT_SAVE_VNGRAPH_ON_PATCH")),
        trace_events=args.trace_events or parse_bool(get("IFLINE_AGENT_TRACE_EVENTS")),
    )


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def resolve_vngraph_tool_project(configured_path: str) -> Path:
    if configured_path.strip():
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise RuntimeError("找不到 C# VNGraph 前端工具: " + str(path))
        return path

    candidates = [
        REPO_ROOT / "IfLine" / "tools" / "VNGraphFrontendTool" / "VNGraphFrontendTool.csproj",
        Path.cwd() / "IfLine" / "tools" / "VNGraphFrontendTool" / "VNGraphFrontendTool.csproj",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise RuntimeError(
        "找不到 C# VNGraph 前端工具，请配置 IFLINE_AGENT_VNGRAPH_TOOL_PROJECT "
        "或传入 --vn-graph-tool-project"
    )


def apply_patch_with_csharp_frontend(
    graph_json: dict[str, Any],
    patch: list[dict[str, Any]],
    tool_project: Path,
) -> dict[str, Any]:
    if not tool_project.exists():
        raise RuntimeError("找不到 C# VNGraph 前端工具: " + str(tool_project))
    with tempfile.TemporaryDirectory(prefix="ifline-vngraph-frontend-") as temp_dir:
        temp_path = Path(temp_dir)
        input_path = temp_path / "graph.json"
        patch_path = temp_path / "patch.json"
        output_path = temp_path / "patched_graph.json"
        input_path.write_text(json.dumps(graph_json, ensure_ascii=False), encoding="utf-8")
        patch_path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run(
            [
                "dotnet",
                "run",
                "--project",
                str(tool_project),
                "--",
                "--input",
                str(input_path),
                "--patch",
                str(patch_path),
                "--output",
                str(output_path),
            ],
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        stdout = completed.stdout.strip()
        try:
            summary = json.loads(stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "C# VNGraph 前端工具输出不是 JSON: "
                + stdout[:1000]
                + "\nstderr:\n"
                + completed.stderr[:2000]
            ) from exc
        if completed.returncode != 0 or not summary.get("ok"):
            return {
                "ok": False,
                "returncode": completed.returncode,
                "summary": summary,
                "stderr": completed.stderr,
            }
        patched_graph = json.loads(output_path.read_text(encoding="utf-8-sig"))
        return {
            "ok": True,
            "graph_json": patched_graph,
            "result_graph_hash": summary.get("result_graph_hash"),
            "summary": summary,
        }


def print_thread_list(items: list[dict[str, Any]]) -> None:
    if not items:
        print("没有匹配的 Agent thread")
        return
    for index, item in enumerate(items, start=1):
        print(
            "{index}. {thread_id} status={status} kind={kind} messages={messages} updated_at={updated}".format(
                index=index,
                thread_id=item.get("thread_id") or "",
                status=item.get("status") or "",
                kind=item.get("agent_kind") or "",
                messages=item.get("persisted_message_count") or 0,
                updated=item.get("updated_at") or "",
            )
        )


def parse_key_args(args: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in args:
        if "=" not in item:
            raise ValueError("过滤参数必须是 key=value: " + item)
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def hash_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def quote_path(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="if_line 服务端 Agent 假前端控制台")
    parser.add_argument("--env-file", default=str(ROOT / "tools" / "server_agent_client.env"))
    parser.add_argument("--base-url")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--guest", action="store_true")
    parser.add_argument("--agent-kind")
    parser.add_argument("--mode")
    parser.add_argument("--thread-id")
    parser.add_argument("--resume", action="store_true", help="启动时自动切到当前用户最近的 Agent thread")
    parser.add_argument("--resume-status")
    parser.add_argument("--resume-agent-kind")
    parser.add_argument("--resume-limit")
    parser.add_argument("--timeout")
    parser.add_argument("--poll-interval")
    parser.add_argument("--project-id")
    parser.add_argument("--chapter-index")
    parser.add_argument("--vn-graph-revision-id")
    parser.add_argument("--node-index")
    parser.add_argument("--vn-graph-file")
    parser.add_argument("--vn-graph-tool-project")
    parser.add_argument("--save-vngraph-on-patch", action="store_true")
    parser.add_argument("--trace-events", action="store_true")
    return parser


def main() -> int:
    config = load_config(build_parser().parse_args())
    console = AgentConsole(config)
    console.start()
    console.run_shell()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
