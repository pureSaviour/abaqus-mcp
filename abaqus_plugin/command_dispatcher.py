# -*- coding: utf-8 -*-
"""
abaqus_plugin/command_dispatcher.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
将命令类型路由到各处理模块。
新增命令类型只需在此注册，无需修改轮询逻辑。
"""

import time
import traceback

from ipc_shared import CmdType, ErrorCode, STOP_FILE, ok_response, err_response

# 延迟导入各处理模块（避免 Abaqus 环境加载失败时影响整体）
def _get_handlers():
    from abaqus_plugin.script_executor  import execute_script
    from abaqus_plugin.model_inspector  import get_model_info
    from abaqus_plugin.job_manager      import list_jobs, get_job_status, submit_job
    from abaqus_plugin.odb_reader       import get_odb_info, query_odb_results
    from abaqus_plugin.viewport_capture import get_viewport_image
    return {
        CmdType.EXECUTE_SCRIPT:     execute_script,
        CmdType.GET_MODEL_INFO:     get_model_info,
        CmdType.LIST_JOBS:          list_jobs,
        CmdType.GET_JOB_STATUS:     get_job_status,
        CmdType.SUBMIT_JOB:         submit_job,
        CmdType.GET_ODB_INFO:       get_odb_info,
        CmdType.QUERY_ODB_RESULTS:  query_odb_results,
        CmdType.GET_VIEWPORT_IMAGE: get_viewport_image,
    }


def dispatch(command: dict) -> dict:
    """
    分发一条命令到对应处理函数。

    内置 ping / stop 命令直接处理，其余命令路由到各模块。
    """
    cmd_id   = command.get("id", "unknown")
    cmd_type = command.get("type", "")

    # ---- 内置命令 ----
    if cmd_type == CmdType.PING:
        from ipc_shared import PLUGIN_VERSION
        return ok_response(cmd_id, data={
            "response":  "pong",
            "version":   PLUGIN_VERSION,
            "timestamp": time.time(),
        })

    if cmd_type == CmdType.STOP:
        try:
            STOP_FILE.write_text("stop", encoding="utf-8")
        except Exception:
            pass
        return ok_response(cmd_id, data={"message": "Stop signal written"})

    # ---- 路由到处理模块 ----
    try:
        handlers = _get_handlers()
    except Exception as e:
        return err_response(cmd_id, ErrorCode.INTERNAL_ERROR,
                            f"Handler import failed: {e}", traceback.format_exc())

    handler = handlers.get(cmd_type)
    if handler is None:
        return err_response(
            cmd_id, ErrorCode.UNKNOWN_COMMAND,
            f"Unknown command type: '{cmd_type}'. "
            f"Available: {list(handlers.keys()) + [CmdType.PING, CmdType.STOP]}"
        )

    return handler(command)
