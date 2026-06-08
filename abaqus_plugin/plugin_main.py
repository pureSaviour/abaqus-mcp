# -*- coding: utf-8 -*-
"""
abaqus_plugin/plugin_main.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Abaqus/CAE 侧插件主入口。

使用方法（在 Abaqus 控制台）：
    File -> Run Script -> .../abaqus_plugin/plugin_main.py

加载后可用命令：
    mcp_start()      # 后台线程模式（推荐，GUI 保持响应）
    mcp_loop()       # 阻塞模式（兼容性最好）
    mcp_coop_loop()  # 协作模式（GUI 大部分保持响应）
    mcp_stop()       # 停止
    mcp_status()     # 查看状态
"""

import sys
import time
import threading
import os
import traceback
from pathlib import Path

# 把项目根加入 sys.path（插件文件在 abaqus_plugin/ 子目录）
_THIS_DIR    = Path(__file__).parent.resolve()
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ipc_shared import (
    PLUGIN_VERSION, STOP_FILE,
    POLL_INTERVAL, HEARTBEAT_INTERVAL, STALE_CMD_AGE,
)
from abaqus_plugin.ipc_handler import (
    ensure_dirs, write_status, poll_once, cleanup_stale_commands, _log,
)
from abaqus_plugin.command_dispatcher import dispatch

# ---------------------------------------------------------------------------
# 全局状态（单元素列表作为可变引用，避免 global 声明）
# ---------------------------------------------------------------------------
_state = {
    "running":         False,
    "cmds_processed":  [0],    # 用列表包装以便 poll_once 修改
    "start_time":      0.0,
    "thread":          None,
    "generation":      0,      # 用于失效旧线程
}


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _is_alive(t) -> bool:
    if t is None:
        return False
    try:
        return t.is_alive()
    except Exception:
        return False


def _heartbeat_message() -> str:
    uptime = int(time.time() - _state["start_time"]) if _state["start_time"] else 0
    return (f"Polling active | cmds={_state['cmds_processed'][0]} "
            f"uptime={uptime}s")


def _thread_loop(generation: int) -> None:
    """后台线程轮询循环。"""
    last_heartbeat = 0.0
    last_cleanup   = 0.0

    try:
        while _state["running"] and _state["generation"] == generation:
            now = time.time()

            # 心跳
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                write_status("running", _heartbeat_message(),
                             _state["start_time"], _state["cmds_processed"][0])
                last_heartbeat = now

            # 定期清理过期命令
            if now - last_cleanup >= 30.0:
                cleanup_stale_commands()
                last_cleanup = now

            # 检查停止标志
            if STOP_FILE.exists():
                try:
                    STOP_FILE.unlink(missing_ok=True)
                except Exception:
                    pass
                _state["running"] = False
                print("[MCP] Stopped by stop.flag")
                _log("INFO", "Stopped by stop.flag")
                break

            poll_once(dispatch, _state["cmds_processed"])
            time.sleep(POLL_INTERVAL)

    except Exception as e:
        _log("ERROR", f"Thread loop error: {e}\n{traceback.format_exc()}")
        print(f"[MCP] Background worker error: {e}")
    finally:
        if _state["generation"] == generation:
            _state["running"] = False
            _state["thread"]  = None
        write_status("stopped", "Polling stopped",
                     _state["start_time"], _state["cmds_processed"][0])
        print("[MCP] Background loop ended")
        _log("INFO", "Background loop ended")




def mcp_start(interval: float = POLL_INTERVAL) -> None:
    """
    启动后台线程模式（推荐）。
    Abaqus GUI 保持响应。部分版本的 Abaqus 可能不支持后台线程，
    如果 ping 自检失败会提示改用 mcp_loop()。
    """
    if _state["running"]:
        print("[MCP] Already running. Call mcp_stop() first.")
        return

    if STOP_FILE.exists():
        STOP_FILE.unlink(missing_ok=True)

    ensure_dirs()
    _state["cmds_processed"][0] = 0
    _state["start_time"]        = time.time()
    _state["generation"]        += 1
    _state["running"]           = True
    generation = _state["generation"]

    t = threading.Thread(
        target=_thread_loop,
        args=(generation,),
        daemon=True,
    )
    _state["thread"] = t
    t.start()

    # 等一下让线程跑起来
    time.sleep(0.1)
    if not _is_alive(t):
        _state["running"] = False
        _state["thread"]  = None
        write_status("error", "Background thread exited immediately")
        print("[MCP] ERROR: Background thread failed to start. Use mcp_loop() instead.")
        _log("ERROR", "Background thread exited during startup")
        return

    write_status("running", "Polling active (background)",
                 _state["start_time"], _state["cmds_processed"][0])
    print(f"[MCP] Started in background mode (interval={interval}s)")
    print("[MCP] Use mcp_stop() to stop.")
    _log("INFO", "Started in background mode")

    # 自检
    _self_test()


def mcp_loop(interval: float = POLL_INTERVAL) -> None:
    """
    阻塞轮询模式。
    兼容性最好，但会阻塞 Abaqus console。
    停止方法：在 PowerShell 中执行：
        echo $null > "$env:ABAQUS_MCP_HOME\\stop.flag"
    """
    if STOP_FILE.exists():
        STOP_FILE.unlink(missing_ok=True)

    ensure_dirs()
    _state["cmds_processed"][0] = 0
    _state["start_time"]        = time.time()
    _state["running"]           = True

    write_status("running", "Polling active (blocking)",
                 _state["start_time"], _state["cmds_processed"][0])
    _log("INFO", "Started in blocking mode")
    print("[MCP] Listening... (blocking mode)")
    print(f"[MCP] Stop: create file '{STOP_FILE}'")

    last_heartbeat = 0.0
    last_cleanup   = 0.0

    try:
        while True:
            now = time.time()

            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                write_status("running", _heartbeat_message(),
                             _state["start_time"], _state["cmds_processed"][0])
                last_heartbeat = now

            if now - last_cleanup >= 30.0:
                cleanup_stale_commands()
                last_cleanup = now

            if STOP_FILE.exists():
                STOP_FILE.unlink(missing_ok=True)
                print("[MCP] Stopped by stop.flag")
                break

            poll_once(dispatch, _state["cmds_processed"])
            time.sleep(max(0.02, float(interval)))

    except KeyboardInterrupt:
        print("\n[MCP] Stopped by Ctrl+C")
    except Exception as e:
        print(f"[MCP] Error: {e}")
        _log("ERROR", f"mcp_loop: {e}")
    finally:
        _state["running"] = False
        write_status("stopped", "Polling stopped",
                     _state["start_time"], _state["cmds_processed"][0])
        _log("INFO", "Blocking loop ended")
        print("[MCP] Loop ended")


def mcp_coop_loop(interval: float = POLL_INTERVAL) -> None:
    """
    协作轮询模式。
    通过 session.processUpdates() 保持 GUI 部分响应，
    适合不支持后台线程的 Abaqus 版本。
    """
    try:
        from abaqus import session  # noqa
        has_session = True
    except ImportError:
        has_session = False

    if STOP_FILE.exists():
        STOP_FILE.unlink(missing_ok=True)

    ensure_dirs()
    _state["cmds_processed"][0] = 0
    _state["start_time"]        = time.time()
    _state["running"]           = True

    write_status("running", "Polling active (cooperative)",
                 _state["start_time"], _state["cmds_processed"][0])
    _log("INFO", "Started in cooperative mode")
    print("[MCP] Listening... (cooperative mode)")

    last_heartbeat = 0.0
    last_cleanup   = 0.0

    try:
        while True:
            now = time.time()

            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                write_status("running", _heartbeat_message(),
                             _state["start_time"], _state["cmds_processed"][0])
                last_heartbeat = now

            if now - last_cleanup >= 30.0:
                cleanup_stale_commands()
                last_cleanup = now

            if STOP_FILE.exists():
                STOP_FILE.unlink(missing_ok=True)
                print("[MCP] Stopped by stop.flag")
                break

            poll_once(dispatch, _state["cmds_processed"])

            if has_session:
                try:
                    session.processUpdates()
                except Exception:
                    pass

            time.sleep(max(0.02, float(interval)))

    except KeyboardInterrupt:
        print("\n[MCP] Stopped by Ctrl+C")
    except Exception as e:
        print(f"[MCP] Error: {e}")
        _log("ERROR", f"mcp_coop_loop: {e}")
    finally:
        _state["running"] = False
        write_status("stopped", "Polling stopped",
                     _state["start_time"], _state["cmds_processed"][0])
        _log("INFO", "Cooperative loop ended")
        print("[MCP] Cooperative loop ended")


def mcp_stop() -> None:
    """停止所有轮询模式。"""
    _state["running"]   = False
    _state["generation"] += 1  # 失效旧线程

    try:
        STOP_FILE.write_text("stop", encoding="utf-8")
    except Exception:
        pass

    t = _state["thread"]
    if _is_alive(t):
        t.join(timeout=2.0)
    _state["thread"] = None

    write_status("stopped", "Polling stopped",
                 _state["start_time"], _state["cmds_processed"][0])
    print("[MCP] Stopped")
    _log("INFO", "Stop signal sent")


def mcp_status() -> None:
    """打印当前 MCP 状态。"""
    sep = "=" * 55
    print(f"\n{sep}")
    print(f" Abaqus MCP Plugin v{PLUGIN_VERSION}")
    print(sep)
    print(f" Running:    {_state['running']}")
    print(f" Thread:     {'alive' if _is_alive(_state['thread']) else 'none'}")
    print(f" Commands:   {_state['cmds_processed'][0]}")
    if _state["start_time"]:
        uptime = int(time.time() - _state["start_time"])
        print(f" Uptime:     {uptime}s")
    print(f" MCP Home:   {_PROJECT_ROOT}")
    print(f"\n Commands:")
    print("   mcp_start()      - background thread (recommended)")
    print("   mcp_loop()       - blocking mode")
    print("   mcp_coop_loop()  - cooperative mode")
    print("   mcp_stop()       - stop")
    print("   mcp_status()     - this message")
    print(sep)


# ---------------------------------------------------------------------------
# 后台模式自检
# ---------------------------------------------------------------------------

def _self_test(timeout: float = 2.0) -> None:
    """发一条 ping 给自己，验证后台线程确实在消费命令文件。"""
    import uuid, json
    from ipc_shared import COMMANDS_DIR, RESULTS_DIR, CMD_PREFIX

    test_id  = "selftest_" + uuid.uuid4().hex[:8]
    cmd_path = COMMANDS_DIR / f"{CMD_PREFIX}{test_id}.json"
    res_path = RESULTS_DIR  / f"{test_id}.json"

    try:
        cmd_path.write_text(
            json.dumps({"id": test_id, "type": "ping", "timestamp": time.time()}),
            encoding="utf-8"
        )
    except Exception:
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        if res_path.exists():
            try:
                res_path.unlink(missing_ok=True)
            except Exception:
                pass
            print("[MCP] Background self-test passed.")
            return
        time.sleep(0.05)

    # 清理
    cmd_path.unlink(missing_ok=True)
    print("[MCP] WARNING: Background self-test failed. "
          "Try mcp_coop_loop() or mcp_loop() for better compatibility.")
    _log("WARN", "Background self-test failed")


# ---------------------------------------------------------------------------
# 插件加载时自动初始化
# ---------------------------------------------------------------------------

ensure_dirs()
write_status("ready", f"Plugin loaded v{PLUGIN_VERSION}")

def print_hello():
    print("")
    print("=" * 55)
    print(f" Abaqus MCP Plugin v{PLUGIN_VERSION}")
    print("=" * 55)
    print(f" Home: {_PROJECT_ROOT}")
    print("")
    print(" Start: mcp_start()  (background, recommended)")
    print("        mcp_loop()   (blocking, most compatible)")
    print(" Stop:  mcp_stop()")
    print(" Info:  mcp_status()")
    print("=" * 55)

print_hello()
_log("INFO", f"Plugin loaded v{PLUGIN_VERSION}")
