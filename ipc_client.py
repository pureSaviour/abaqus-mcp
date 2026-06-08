# -*- coding: utf-8 -*-
"""
ipc_client.py  (Server 侧)
~~~~~~~~~~~~~~~~~~~~~~~~~~
负责向 Abaqus 插件发送命令并等待结果。
运行在外部 Python 环境（MCP server 进程）中。
"""

import json
import time
import uuid
from pathlib import Path

from ipc_shared import (
    COMMANDS_DIR, RESULTS_DIR, STATUS_FILE,
    CMD_PREFIX, DEFAULT_TIMEOUT, STALE_CMD_AGE,
    ErrorCode, err_response
)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _ensure_dirs() -> None:
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _write_json_atomic(path: Path, data: dict) -> None:
    """原子写入：先写 .tmp，再 rename，防止 plugin 读到半写文件。"""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_status() -> dict:
    """读取 plugin 心跳状态文件。"""
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 核心发送函数
# ---------------------------------------------------------------------------

def send_command(cmd_type: str, timeout: float = DEFAULT_TIMEOUT,
                 **kwargs) -> dict:
    """
    发送一条命令给 Abaqus 插件，同步等待结果。

    Parameters
    ----------
    cmd_type : str
        命令类型，见 CmdType。
    timeout : float
        等待结果的最长秒数。
    **kwargs
        命令附带的参数，会合并到命令 JSON 中。

    Returns
    -------
    dict
        标准响应字典，包含 success / error_code / data / output 等字段。
    """
    _ensure_dirs()

    cmd_id = uuid.uuid4().hex[:12]
    command = {
        "id":        cmd_id,
        "type":      cmd_type,
        "timestamp": time.time(),
        **kwargs,
    }

    cmd_path    = COMMANDS_DIR / f"{CMD_PREFIX}{cmd_id}.json"
    result_path = RESULTS_DIR  / f"{cmd_id}.json"

    _write_json_atomic(cmd_path, command)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                # 结果读取成功后删除，防止堆积
                try:
                    result_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return result
            except json.JSONDecodeError:
                # 文件可能还在写入中，稍等再读
                time.sleep(0.02)
                continue

        time.sleep(0.05)

    # 超时：清理悬空命令文件
    try:
        cmd_path.unlink(missing_ok=True)
    except Exception:
        pass

    return err_response(
        cmd_id,
        ErrorCode.TIMEOUT,
        f"No response from Abaqus plugin within {timeout}s. "
        "Make sure the plugin is running (call mcp_start() in Abaqus console)."
    )


# ---------------------------------------------------------------------------
# 连接状态检查
# ---------------------------------------------------------------------------

def check_plugin_alive(ping_timeout: float = 10.0) -> tuple[bool, str]:
    """
    检查插件是否在线。
    先读心跳文件快速判断，再发 ping 命令验证响应。

    Returns
    -------
    (alive: bool, message: str)
    """
    status = _read_status()

    if not status:
        return False, (
            "status.json not found. Plugin is not loaded.\n"
            "In Abaqus console: File -> Run Script -> abaqus_plugin/plugin_main.py\n"
            "Then run: mcp_start()"
        )

    plugin_status = status.get("status", "unknown")
    if plugin_status != "running":
        return False, (
            f"Plugin loaded but not running (status={plugin_status!r}).\n"
            "Run mcp_start() in Abaqus console."
        )

    # 心跳时间戳检查：超过 10 秒没更新说明挂了
    ts = status.get("timestamp", 0.0)
    if time.time() - ts > 10.0:
        return False, (
            f"Plugin heartbeat stale ({time.time() - ts:.0f}s ago). "
            "Plugin may have crashed. Try mcp_stop() then mcp_start()."
        )

    # 发 ping 验证实际响应
    result = send_command("ping", timeout=ping_timeout)
    if result.get("success"):
        ver = (result.get("data") or {}).get("version", "?")
        return True, f"Connected. Plugin v{ver} | uptime={status.get('uptime_s', '?')}s"
    else:
        return False, f"Ping failed: {result.get('error', 'unknown')}"


# ---------------------------------------------------------------------------
# 过期命令清理（可选，由 server 侧定期调用）
# ---------------------------------------------------------------------------

def cleanup_stale_commands() -> int:
    """清除超过 STALE_CMD_AGE 的滞留命令文件，返回清除数量。"""
    if not COMMANDS_DIR.exists():
        return 0
    now = time.time()
    count = 0
    for f in COMMANDS_DIR.glob(f"{CMD_PREFIX}*.json"):
        try:
            if now - f.stat().st_mtime > STALE_CMD_AGE:
                f.unlink(missing_ok=True)
                count += 1
        except Exception:
            pass
    return count
