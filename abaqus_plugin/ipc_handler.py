# -*- coding: utf-8 -*-
"""
abaqus_plugin/ipc_handler.py  (Plugin 侧)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
运行在 Abaqus/CAE 内置 Python 中。
负责：
  - 扫描 commands/ 目录，读取命令
  - 分发给 command_dispatcher
  - 把结果原子写入 results/
  - 维护 status.json 心跳
  - 日志写入 mcp.log
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# Plugin 侧需要手动把项目根加入 sys.path，才能 import ipc_shared
_PLUGIN_DIR = Path(__file__).parent.resolve()
# _PLUGIN_DIR = Path(os.path.abspath(__file__)).parent.resolve()
_PROJECT_ROOT = _PLUGIN_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ipc_shared import (
    COMMANDS_DIR, RESULTS_DIR, STATUS_FILE, LOG_FILE, STOP_FILE,
    CMD_PREFIX, STALE_CMD_AGE, HEARTBEAT_INTERVAL,
    PLUGIN_VERSION, ErrorCode, err_response
)


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def _log(level: str, message: str) -> None:
    """追加写日志，绝不抛异常。"""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {level}: {message}\n"
        with open(str(LOG_FILE), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 状态心跳
# ---------------------------------------------------------------------------

def write_status(status: str, message: str = "",
                 start_time: float = 0.0, cmds_processed: int = 0) -> None:
    """
    原子写入 status.json。
    使用 .tmp + rename 保证外部读取不会看到半写状态。
    """
    uptime = int(time.time() - start_time) if start_time else 0
    payload = {
        "status":         status,
        "message":        message,
        "version":        PLUGIN_VERSION,
        "timestamp":      time.time(),
        "datetime":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pid":            os.getpid(),
        "uptime_s":       uptime,
        "cmds_processed": cmds_processed,
    }
    tmp = Path(str(STATUS_FILE) + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        # Windows 上 Path.replace 会原子替换
        tmp.replace(STATUS_FILE)
    except Exception:
        # 回退：直接写（非原子，但总比不写好）
        try:
            STATUS_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception:
            pass
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 命令文件读取（带重试，容忍写入中的文件）
# ---------------------------------------------------------------------------

def _load_command(cmd_path: Path, retries: int = 3, delay: float = 0.03) -> dict | None:
    """
    读取并解析命令 JSON 文件。
    带重试逻辑，防止 server 侧还没写完就被读取。
    """
    for _ in range(retries):
        try:
            text = cmd_path.read_text(encoding="utf-8-sig")
            return json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            time.sleep(delay)
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# 结果写入
# ---------------------------------------------------------------------------

def write_result(result: dict) -> None:
    """将结果原子写入 results/<cmd_id>.json。"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cmd_id = result.get("id", "unknown")
    result_path = RESULTS_DIR / f"{cmd_id}.json"
    tmp = result_path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        tmp.replace(result_path)
    except Exception as e:
        _log("ERROR", f"write_result failed for {cmd_id}: {e}")


# ---------------------------------------------------------------------------
# 过期命令清理
# ---------------------------------------------------------------------------

def cleanup_stale_commands() -> None:
    """删除滞留超过 STALE_CMD_AGE 秒的命令文件（防止重启后误执行旧命令）。"""
    if not COMMANDS_DIR.exists():
        return
    now = time.time()
    for f in COMMANDS_DIR.glob(f"{CMD_PREFIX}*.json"):
        try:
            age = now - f.stat().st_mtime
            if age > STALE_CMD_AGE:
                f.unlink(missing_ok=True)
                _log("WARN", f"Removed stale command: {f.name} (age={age:.0f}s)")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 目录初始化
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    for d in [COMMANDS_DIR, RESULTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 主轮询函数（供 plugin_main 调用）
# ---------------------------------------------------------------------------

def poll_once(dispatcher_func, cmds_processed_ref: list) -> bool:
    """
    扫描 commands/ 目录，取最旧的一条命令处理。

    Parameters
    ----------
    dispatcher_func
        命令分发函数，签名 (command: dict) -> dict。
    cmds_processed_ref
        用单元素列表传递可变计数器（避免 nonlocal/global）。

    Returns
    -------
    bool
        True 表示处理了一条命令，False 表示队列为空。
    """
    if not COMMANDS_DIR.exists():
        return False

    # 取所有命令文件，按修改时间升序（FIFO）
    cmd_files = sorted(
        COMMANDS_DIR.glob(f"{CMD_PREFIX}*.json"),
        key=lambda p: p.stat().st_mtime
    )
    if not cmd_files:
        return False

    cmd_path = cmd_files[0]
    command = _load_command(cmd_path)

    if command is None:
        # 读取失败，跳过（不删除，等下次重试；若持续失败会被 stale 清理）
        _log("WARN", f"Failed to parse command file: {cmd_path.name}")
        return False

    cmd_id   = command.get("id", "unknown")
    cmd_type = command.get("type", "unknown")

    # 先删命令文件，防止 plugin 重启后重复执行
    try:
        cmd_path.unlink(missing_ok=True)
    except Exception as e:
        _log("WARN", f"Could not delete command file {cmd_path.name}: {e}")

    # 分发执行
    try:
        result = dispatcher_func(command)
    except Exception as e:
        tb = traceback.format_exc()
        _log("ERROR", f"dispatcher raised for {cmd_type}/{cmd_id}: {e}")
        result = err_response(cmd_id, ErrorCode.INTERNAL_ERROR, str(e), tb)

    write_result(result)
    cmds_processed_ref[0] += 1

    status_str = "OK" if result.get("success") else f"FAIL({result.get('error_code', '?')})"
    if cmd_type != "ping":
        _log("INFO", f"{cmd_type} [{status_str}] id={cmd_id}")
        print(f"[MCP] {cmd_type} [{status_str}]")

    return True
