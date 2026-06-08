# -*- coding: utf-8 -*-
"""
ipc_shared.py
~~~~~~~~~~~~~
Server 侧和 Plugin 侧共享的常量、枚举和数据结构。
两侧都 import 这个文件，保证协议一致。
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

def resolve_mcp_home() -> Path:
    """
    解析 MCP 工作目录。优先级：
    1. 环境变量 ABAQUS_MCP_HOME（推荐，支持自定义路径）
    2. 脚本所在目录（如果包含 sentinel 文件则判定为项目根）
    3. 默认 ~/.abaqus-mcp/
    """
    env_home = os.environ.get("ABAQUS_MCP_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()

    # 尝试从文件位置推断（plugin 侧常见场景）
    try:
        this_dir = Path(__file__).parent.resolve()
        if (this_dir / "mcp_server.py").exists() or (this_dir / "abaqus_plugin").exists():
            return this_dir
    except Exception:
        pass

    return Path.home() / ".abaqus-mcp"


MCP_HOME: Path = resolve_mcp_home()

COMMANDS_DIR  = MCP_HOME / "commands"
RESULTS_DIR   = MCP_HOME / "results"
SCRIPTS_DIR   = MCP_HOME / "scripts"
SCREENSHOTS_DIR = MCP_HOME / "screenshots"
STATUS_FILE   = MCP_HOME / "status.json"
STOP_FILE     = MCP_HOME / "stop.flag"
LOG_FILE      = MCP_HOME / "mcp.log"


# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------

PLUGIN_VERSION = "1.0.0"

# 命令文件前缀
CMD_PREFIX = "cmd_"

# 命令超时（秒）。server 侧默认值，各工具可覆盖。
DEFAULT_TIMEOUT   = 30.0
JOB_TIMEOUT       = 600.0   # 作业提交等待上限
ODB_TIMEOUT       = 60.0    # ODB 文件打开
VIEWPORT_TIMEOUT  = 30.0
STALE_CMD_AGE     = 120.0   # 超过此时间的命令文件视为过期并清除

# 状态心跳间隔（秒）
HEARTBEAT_INTERVAL = 2.0

# 轮询间隔（秒）
POLL_INTERVAL = 0.1


# ---------------------------------------------------------------------------
# 命令类型枚举（字符串常量，避免拼写错误）
# ---------------------------------------------------------------------------

class CmdType:
    PING               = "ping"
    STOP               = "stop"
    EXECUTE_SCRIPT     = "execute_script"
    GET_MODEL_INFO     = "get_model_info"
    LIST_JOBS          = "list_jobs"
    SUBMIT_JOB         = "submit_job"
    GET_JOB_STATUS     = "get_job_status"
    GET_ODB_INFO       = "get_odb_info"
    QUERY_ODB_RESULTS  = "query_odb_results"
    GET_VIEWPORT_IMAGE = "get_viewport_image"
    CREATE_SPECIMEN    = "create_specimen"   # 高层建模（阶段3）


# ---------------------------------------------------------------------------
# 错误码枚举（结构化错误，比纯字符串更利于 LLM 判断）
# ---------------------------------------------------------------------------

class ErrorCode:
    # 通用
    OK                = "OK"
    TIMEOUT           = "TIMEOUT"
    UNKNOWN_COMMAND   = "UNKNOWN_COMMAND"
    INTERNAL_ERROR    = "INTERNAL_ERROR"

    # 连接
    PLUGIN_NOT_RUNNING = "PLUGIN_NOT_RUNNING"
    ABAQUS_NOT_FOUND   = "ABAQUS_NOT_FOUND"

    # 脚本执行
    SCRIPT_SYNTAX_ERROR    = "SCRIPT_SYNTAX_ERROR"
    SCRIPT_RUNTIME_ERROR   = "SCRIPT_RUNTIME_ERROR"

    # 作业
    JOB_NOT_FOUND     = "JOB_NOT_FOUND"
    JOB_ABORTED       = "JOB_ABORTED"
    JOB_SUBMIT_FAILED = "JOB_SUBMIT_FAILED"

    # ODB
    ODB_NOT_FOUND     = "ODB_NOT_FOUND"
    ODB_OPEN_FAILED   = "ODB_OPEN_FAILED"
    ODB_FIELD_MISSING = "ODB_FIELD_MISSING"

    # 视口
    VIEWPORT_NOT_FOUND = "VIEWPORT_NOT_FOUND"
    VIEWPORT_CAPTURE_FAILED = "VIEWPORT_CAPTURE_FAILED"

    # 建模
    INVALID_PARAMS    = "INVALID_PARAMS"
    MODEL_EXISTS      = "MODEL_EXISTS"


# ---------------------------------------------------------------------------
# 响应构造辅助函数
# ---------------------------------------------------------------------------

def ok_response(cmd_id: str, data=None, output: str = "") -> dict:
    """构造成功响应。"""
    return {
        "id":      cmd_id,
        "success": True,
        "error_code": ErrorCode.OK,
        "error":   None,
        "data":    data,
        "output":  output,
    }


def err_response(cmd_id: str, error_code: str, message: str,
                 traceback_str: str = "") -> dict:
    """构造失败响应。"""
    return {
        "id":        cmd_id,
        "success":   False,
        "error_code": error_code,
        "error":     message,
        "traceback": traceback_str,
        "data":      None,
        "output":    "",
    }
