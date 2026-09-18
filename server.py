"""
╔══════════════════════════════════════════════════════════════╗
║  Claude Code 远程控制服务端 v1.0                             ║
║  手机 → WebSocket → 本机 Claude Code 实时通信                ║
║  基于 Python FastAPI + WebSocket                             ║
╚══════════════════════════════════════════════════════════════╝

启动命令: python server.py
依赖安装: pip install fastapi uvicorn websockets
"""

import asyncio
import json
import os
import sys
import time
import uuid
import re
import subprocess
import logging
import secrets
import platform
import shutil
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, AsyncGenerator, Dict, Any, List
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, status, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── Windows 异步子进程兼容 ──────────────────────────────────
if sys.platform == "win32" and sys.version_info < (3, 8):
    # Python 3.7 默认 SelectorEventLoop 不支持子进程，切换到 ProactorEventLoop
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    log = logging.getLogger("claude-remote")
    log.info("已设置 Windows ProactorEventLoop（Python 3.7 子进程兼容）")

# ── 日志配置 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("claude-remote")

# ── 常量 ──────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "config.json"
HISTORY_PATH = BASE_DIR / "history.json"
STATIC_DIR = BASE_DIR / "static"
MAX_HISTORY = 500
DEFAULT_PORT = 8765

# ── 配置管理 ──────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "server": {
        "host": "0.0.0.0",
        "port": DEFAULT_PORT,
        "auth_token": "",  # 首次启动自动生成
    },
    "claude": {
        "command": "claude",
        "timeout_seconds": 600,
        "strip_ansi": True,
        "model": "deepseek-v4-flash",
    },
    "security": {
        "lan_only": False,
        "allowed_ips": [],
        "rate_limit_per_minute": 60,
    },
}


def load_config() -> dict:
    """加载配置，首次运行自动生成默认配置 + 随机 token"""
    if not CONFIG_PATH.exists():
        config = DEFAULT_CONFIG.copy()
        config["server"]["auth_token"] = secrets.token_hex(16)
        save_config(config)
        log.info("🔑 已生成新认证 Token: %s", config["server"]["auth_token"])
        return config
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("配置读取失败，使用默认配置: %s", e)
        cfg = DEFAULT_CONFIG.copy()
        cfg["server"]["auth_token"] = secrets.token_hex(16)
        return cfg


def save_config(config: dict):
    """保存配置到文件"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    log.info("配置已保存: %s", CONFIG_PATH)


# ── 任务历史管理 ──────────────────────────────────────────────
def load_history() -> list:
    """加载任务历史"""
    if not HISTORY_PATH.exists():
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return data.get("tasks", [])
    except Exception:
        return []


def save_history(tasks: list):
    """保存任务历史，自动裁剪上限"""
    if len(tasks) > MAX_HISTORY:
        tasks = tasks[-MAX_HISTORY:]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f, ensure_ascii=False, indent=2)


def add_task_to_history(task: dict):
    """添加一条任务记录到历史"""
    tasks = load_history()
    tasks.append(task)
    save_history(tasks)


def update_task_in_history(task_id: str, updates: dict):
    """更新历史中的任务记录"""
    tasks = load_history()
    for t in tasks:
        if t.get("id") == task_id:
            t.update(updates)
            break
    save_history(tasks)


# ── ANSI 转义码剥离 ──────────────────────────────────────────
ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


# ── Claude 进程管理器 ─────────────────────────────────────────
class ClaudeRunner:
    """管理 Claude Code 子进程的运行"""

    def __init__(self, config: dict):
        self.config = config
        self._process: Optional[asyncio.subprocess.Process] = None
        self._task_id: Optional[str] = None
        self._cancelled = False

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def run(
        self, prompt: str, task_id: str, is_command: bool = False
    ) -> AsyncGenerator[dict, None]:
        """
        运行 Claude Code 并流式输出结果。
        Yields: {"type": "output"|"error"|"status", "data": str}
        """
        self._task_id = task_id
        self._cancelled = False

        # 路径解析
        claude_cmd = self._resolve_claude_command()
        timeout = self.config["claude"]["timeout_seconds"]

        # 构建参数
        args = [claude_cmd]
        if is_command:
            # 如果是 / 命令，直接传给 Claude 处理
            args.append(prompt)
        else:
            args.append(prompt)

        log.info("🧠 执行任务 [%s]: %s...", task_id, prompt[:80])

        yield {"type": "status", "data": "running"}

        try:
            # Windows 特殊处理：隐藏控制台窗口（Python 3.8+ 才支持 creationflags）
            kwargs = {}
            if platform.system() == "Windows" and sys.version_info >= (3, 8):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            self._process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(BASE_DIR),
                **kwargs,
            )

            # 读取输出
            full_output = ""
            start_time = time.monotonic()

            while True:
                # 超时检查
                elapsed = time.monotonic() - start_time
                if elapsed > timeout:
                    self._kill_process()
                    yield {
                        "type": "error",
                        "data": f"⏱ 任务超时（{timeout}秒），已自动终止",
                    }
                    break

                # 检查是否被取消
                if self._cancelled:
                    self._kill_process()
                    yield {"type": "status", "data": "cancelled"}
                    break

                # 读取一行输出
                try:
                    line_bytes = await asyncio.wait_for(
                        self._process.stdout.readline(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                if not line_bytes:
                    break

                # 解码（支持 UTF-8 和 GBK）
                line = self._decode_output(line_bytes)
                full_output += line

                if self.config["claude"]["strip_ansi"]:
                    line = strip_ansi(line)

                # 清理后的行不为空才发送
                clean = line.strip()
                if clean:
                    yield {"type": "output", "data": clean}

            # 等待进程结束
            if self._process and self._process.returncode is None:
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._kill_process()

            exit_code = self._process.returncode if self._process else -1

            # 最终输出（如有剩余）
            if self.config["claude"]["strip_ansi"]:
                full_output = strip_ansi(full_output)

            completion_status = "cancelled" if self._cancelled else "completed"
            yield {
                "type": "task_end",
                "data": {
                    "id": task_id,
                    "status": completion_status,
                    "exit_code": exit_code,
                    "duration_ms": int((time.monotonic() - start_time) * 1000),
                    "output": full_output.strip(),
                },
            }
            log.info("✅ 任务完成 [%s] - %s (%dms)", task_id, completion_status, int((time.monotonic() - start_time) * 1000))

        except FileNotFoundError:
            yield {
                "type": "error",
                "data": f"❌ 未找到 Claude Code 命令: {claude_cmd}\n"
                        f"请确认已安装 Claude Code，或修改 config.json 中的 claude.command 配置项。",
            }
            yield {"type": "task_end", "data": {"id": task_id, "status": "failed", "output": ""}}
        except Exception as e:
            log.exception("运行 Claude 时出错")
            yield {"type": "error", "data": f"❌ 执行错误: {str(e)}"}
            yield {"type": "task_end", "data": {"id": task_id, "status": "failed", "output": str(e)}}

    def cancel(self):
        """取消当前运行的任务"""
        self._cancelled = True
        self._kill_process()

    def _kill_process(self):
        """杀死子进程"""
        if self._process and self._process.returncode is None:
            try:
                if platform.system() == "Windows":
                    # Windows 上使用 taskkill 强制终止进程树
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self._process.pid)],
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    self._process.send_signal(signal.SIGTERM)
                    try:
                        asyncio.create_task(self._kill_after_timeout(3))
                    except Exception:
                        pass
            except Exception as e:
                log.warning("终止进程时出错: %s", e)

    async def _kill_after_timeout(self, seconds: int):
        await asyncio.sleep(seconds)
        if self._process and self._process.returncode is None:
            try:
                self._process.kill()
            except Exception:
                pass

    def _resolve_claude_command(self) -> str:
        """解析 Claude Code 命令路径"""
        cmd = self.config["claude"]["command"]
        # 如果是完整路径，直接返回
        if os.path.isabs(cmd) and os.path.exists(cmd):
            return cmd
        # 搜索 PATH
        resolved = shutil.which(cmd)
        if resolved:
            return resolved
        # Windows 上常见安装路径
        if platform.system() == "Windows":
            candidates = [
                os.path.expanduser("~/AppData/Roaming/npm/claude.cmd"),
                os.path.expanduser("~/AppData/Roaming/npm/claude"),
                "C:\\Program Files\\nodejs\\claude.cmd",
                "claude.cmd",
            ]
            for c in candidates:
                if shutil.which(c) or os.path.exists(c):
                    return c
        return cmd  # 返回原值，让系统尝试

    def _decode_output(self, data: bytes) -> str:
        """解码子进程输出，优先 UTF-8 降级到 GBK"""
        for enc in ["utf-8", "gbk", "cp1252", "latin-1"]:
            try:
                return data.decode(enc, errors="replace")
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")


# ── 应用工厂 ──────────────────────────────────────────────────
config = load_config()
runner = ClaudeRunner(config)

# 当前正在运行的任务状态
current_task: Optional[Dict[str, Any]] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("╔═══════════════════════════════════════════════════╗")
    log.info("║   Claude Code 远程控制服务已启动                    ║")
    log.info("╚═══════════════════════════════════════════════════╝")
    log.info("📍 监听地址: http://0.0.0.0:%d", config["server"]["port"])
    log.info("🔑 认证 Token: %s", config["server"]["auth_token"])
    log.info("🤖 Claude 命令: %s", config["claude"]["command"])
    log.info("📁 静态文件: %s", STATIC_DIR)
    yield
    # 关闭时清理
    if runner.is_running:
        runner.cancel()
    log.info("服务已停止。")


app = FastAPI(
    title="Claude Code 远程控制",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - 允许手机浏览器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 认证依赖 ──────────────────────────────────────────────────
def verify_token(token: str) -> bool:
    """验证访问令牌"""
    expected = config["server"]["auth_token"]
    if not expected:
        return True  # 空 token = 不启用认证
    return secrets.compare_digest(token, expected)


def verify_request_token(request: Request):
    """HTTP 请求的 token 验证"""
    # 优先从查询参数获取
    token = request.query_params.get("token")
    if not token:
        # 其次从 header
        token = request.headers.get("X-Auth-Token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少认证 token，请在 URL 添加 ?token=xxx 或在 Header 添加 X-Auth-Token",
        )
    if not verify_token(token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token 无效",
        )


# ── 静态文件路由 ──────────────────────────────────────────────
# 挂载 /static 路径
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(token: str = Query("")):
    """首页 - 返回手机控制面板"""
    # 如果 URL 有 token，注入到页面
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>index.html 未找到</h1><p>请将前端文件放在 static/ 目录下</p>")

    html = index_path.read_text(encoding="utf-8")
    if token:
        html = html.replace(
            "</head>",
            f'<script>const URL_TOKEN = "{token}";</script>\n</head>',
        )
    return HTMLResponse(html)


# ── API 路由 ──────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "version": "1.0.0",
        "claude_running": runner.is_running,
        "model": config["claude"]["model"],
        "uptime": time.time(),
    }


@app.get("/api/config")
async def get_config(request: Request):
    """获取配置信息（不含敏感 token）"""
    verify_request_token(request)
    return {
        "model": config["claude"]["model"],
        "timeout": config["claude"]["timeout_seconds"],
        "version": "1.0.0",
    }


@app.get("/api/history")
async def get_history(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """获取任务历史"""
    verify_request_token(request)
    tasks = load_history()
    # 按时间倒序（最新的在前）
    tasks.reverse()
    page = tasks[offset:offset + limit]
    return {
        "total": len(tasks),
        "limit": limit,
        "offset": offset,
        "tasks": page,
    }


@app.delete("/api/history/{task_id}")
async def delete_history(request: Request, task_id: str):
    """删除单条历史记录"""
    verify_request_token(request)
    tasks = load_history()
    new_tasks = [t for t in tasks if t.get("id") != task_id]
    if len(new_tasks) == len(tasks):
        raise HTTPException(status_code=404, detail="任务不存在")
    save_history(new_tasks)
    return {"status": "deleted", "id": task_id}


@app.delete("/api/history")
async def clear_history(request: Request):
    """清空所有历史记录"""
    verify_request_token(request)
    save_history([])
    return {"status": "cleared"}


@app.post("/api/token/refresh")
async def refresh_token(request: Request):
    """刷新认证 token（需要旧 token 验证）"""
    verify_request_token(request)
    new_token = secrets.token_hex(16)
    config["server"]["auth_token"] = new_token
    save_config(config)
    log.warning("🔑 Token 已刷新！")
    return {"token": new_token, "warning": "请立即更新手机端的连接配置"}


@app.post("/api/claude/model")
async def set_model(request: Request):
    """切换 Claude 模型（更新 CLAUDE.md）"""
    verify_request_token(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 JSON 请求体")

    model_name = body.get("model", "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model 不能为空")

    # 更新运行时配置
    config["claude"]["model"] = model_name

    # 更新 CLAUDE.md
    claude_md = BASE_DIR / "CLAUDE.md"
    try:
        if claude_md.exists():
            content = claude_md.read_text(encoding="utf-8")
            new_content = re.sub(
                r'^model:\s*.*$',
                f"model: {model_name}",
                content,
                flags=re.MULTILINE,
            )
            claude_md.write_text(new_content, encoding="utf-8")
        else:
            claude_md.write_text(f"model: {model_name}\n", encoding="utf-8")

        log.info("🔄 模型切换为: %s", model_name)
        return {"status": "ok", "model": model_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新模型配置失败: {e}")


# ── WebSocket 核心 ────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query("")):
    """
    WebSocket 主端点 - 手机与控制端的实时通信通道

    通信协议 (JSON):
    客户端 -> 服务端:
      {"type": "prompt",  "data": "用户问题..."}
      {"type": "command", "data": "/model xxx"}
      {"type": "cancel",  "data": null}
      {"type": "ping",    "data": null}

    服务端 -> 客户端:
      {"type": "status",    "data": "connected|running|idle"}
      {"type": "output",    "data": "Claude 输出内容..."}
      {"type": "error",     "data": "错误信息"}
      {"type": "task_start", "data": {"id": "xxx", "prompt": "..."}}
      {"type": "task_end",  "data": {"id": "xxx", "status": "completed|failed|cancelled", ...}}
      {"type": "history",   "data": [...]}
      {"type": "pong",      "data": null}
    """
    # ── 鉴权 ──
    if not verify_token(token):
        await websocket.close(code=4001, reason="Token 无效")
        log.warning("❌ WebSocket 连接被拒绝：Token 无效")
        return

    await websocket.accept()
    log.info("📱 手机已连接")

    try:
        # 发送连接成功状态
        await websocket.send_json({
            "type": "status",
            "data": "connected",
            "model": config["claude"]["model"],
            "server_time": datetime.now(timezone.utc).isoformat(),
        })

        # 发送当前任务状态（如果有运行中的）
        if runner.is_running:
            await websocket.send_json({
                "type": "status",
                "data": "busy",
                "message": f"当前有任务正在执行中",
            })

        # ── 消息循环 ──
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                # 心跳保活
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    break
                continue

            # 解析消息
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "data": "消息格式错误，请发送 JSON",
                })
                continue

            msg_type = msg.get("type", "")
            msg_data = msg.get("data", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "cancel":
                if runner.is_running:
                    runner.cancel()
                    await websocket.send_json({
                        "type": "status",
                        "data": "cancelling",
                        "message": "正在取消任务...",
                    })
                else:
                    await websocket.send_json({
                        "type": "status",
                        "data": "idle",
                        "message": "当前没有运行中的任务",
                    })

            elif msg_type in ("prompt", "command"):
                if runner.is_running:
                    await websocket.send_json({
                        "type": "error",
                        "data": "❌ 当前有任务正在执行，请等待完成或先取消",
                    })
                    continue

                # 判断是否为 / 命令
                text = msg_data.strip()
                is_command = msg_type == "command" or text.startswith("/")

                # 处理模型切换命令
                if text.startswith("/model "):
                    model_name = text[7:].strip()
                    try:
                        config["claude"]["model"] = model_name
                        # 更新 CLAUDE.md
                        claude_md = BASE_DIR / "CLAUDE.md"
                        if claude_md.exists():
                            content = claude_md.read_text(encoding="utf-8")
                            new_content = re.sub(
                                r'^model:\s*.*$',
                                f"model: {model_name}",
                                content,
                                flags=re.MULTILINE,
                            )
                            claude_md.write_text(new_content, encoding="utf-8")
                        await websocket.send_json({
                            "type": "status",
                            "data": f"模型已切换为: {model_name}",
                        })
                    except Exception as e:
                        await websocket.send_json({
                            "type": "error",
                            "data": f"切换模型失败: {e}",
                        })
                    continue

                # 处理 /help
                if text == "/help":
                    await websocket.send_json({
                        "type": "output",
                        "data": (
                            "📋 **Claude 远程控制 - 可用命令**\n\n"
                            "/model <名称>  - 切换 Claude 模型\n"
                            "/help         - 显示此帮助\n"
                            "/history      - 查看最近 10 条任务\n"
                            "/clear        - 清空当前输出\n"
                            "其他文本将作为任务发送给 Claude Code 执行"
                        ),
                    })
                    continue

                # 处理 /history
                if text == "/history":
                    tasks = load_history()
                    recent = tasks[-10:] if tasks else []
                    if not recent:
                        msg_text = "📋 暂无任务历史"
                    else:
                        lines = ["📋 **最近任务历史：**\n"]
                        for i, t in enumerate(reversed(recent), 1):
                            prompt = t.get("prompt", "")[:50]
                            status = t.get("status", "?")
                            time_str = t.get("created_at", "")[11:19] if t.get("created_at") else ""
                            lines.append(f"{i}. [{status}] {prompt}  ({time_str})")
                        msg_text = "\n".join(lines)
                    await websocket.send_json({"type": "output", "data": msg_text})
                    continue

                # ── 创建任务并执行 ──
                task_id = f"task_{uuid.uuid4().hex[:12]}"
                now_iso = datetime.now(timezone.utc).isoformat()

                # 通知客户端
                await websocket.send_json({
                    "type": "task_start",
                    "data": {
                        "id": task_id,
                        "prompt": text[:200],
                        "is_command": is_command,
                        "created_at": now_iso,
                    },
                })

                # 记录到历史
                history_entry = {
                    "id": task_id,
                    "prompt": text,
                    "is_command": is_command,
                    "status": "running",
                    "created_at": now_iso,
                    "completed_at": None,
                    "duration_ms": None,
                    "output": "",
                }
                add_task_to_history(history_entry)

                # 执行任务
                async for chunk in runner.run(text, task_id, is_command):
                    try:
                        await websocket.send_json(chunk)
                    except Exception:
                        log.warning("发送 WebSocket 消息失败（客户端可能已断开）")
                        runner.cancel()
                        break

                    # 如果收到 task_end，更新历史
                    if chunk["type"] == "task_end":
                        data = chunk["data"]
                        update_task_in_history(task_id, {
                            "status": data.get("status"),
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                            "duration_ms": data.get("duration_ms"),
                            "output": data.get("output", ""),
                        })

                # 任务结束后发送完成通知
                try:
                    await websocket.send_json({
                        "type": "status",
                        "data": "idle",
                        "message": "任务已完成，等待新指令",
                    })
                except Exception:
                    break

            else:
                await websocket.send_json({
                    "type": "error",
                    "data": f"未知消息类型: {msg_type}",
                })

    except WebSocketDisconnect:
        log.info("📱 手机已断开连接")
    except Exception as e:
        log.exception("WebSocket 错误")
        try:
            await websocket.send_json({"type": "error", "data": f"服务器错误: {str(e)[:200]}"})
        except Exception:
            pass
    finally:
        # 如果当前有任务在运行，取消它
        if runner.is_running:
            log.warning("客户端断开，取消当前任务")
            runner.cancel()


# ── 入口 ──────────────────────────────────────────────────────
def main():
    port = config["server"]["port"]
    host = config["server"]["host"]

    print()
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║      Claude Code 远程控制服务端                    ║")
    print("  ║      Remote Control for Claude Code              ║")
    print("  ╠══════════════════════════════════════════════════╣")
    print(f"  ║  地址: http://{host}:{port}                      ")
    print(f"  ║  Token: {config['server']['auth_token']}")
    print(f"  ║  模型: {config['claude']['model']}")
    print(f"  ║  超时: {config['claude']['timeout_seconds']}秒")
    print("  ╠══════════════════════════════════════════════════╣")
    print("  ║  手机操作指南:                                    ║")
    print("  ║  ① 手机连上同一 WiFi                              ║")
    print(f"  ║  ② 浏览器打开 http://<本机IP>:{port}              ")
    print("  ║  ③ 输入 Token 连接即可使用                        ║")
    print("  ╚══════════════════════════════════════════════════╝")
    print()

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        ws_ping_interval=25,
        ws_ping_timeout=10,
    )


if __name__ == "__main__":
    main()
