# -*- coding: utf-8 -*-
"""
abaqus_plugin/script_executor.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
在 Abaqus kernel 环境中执行任意 Python 脚本。

设计要点（相比 Cai-aa 的改进）：
1. print 拦截用 contextlib.redirect_stdout，比替换 builtins.print 更彻底，
   能捕获子模块的 print（包括 abaqusConstants 内部输出）。
2. exec 的 globals 注入真实的 mdb/session 引用，而不是重新 import，
   保证拿到的是当前 kernel 会话的对象。
3. 语法错误和运行时错误分开返回不同的 error_code。
4. 脚本文件写到 scripts/ 目录便于调试，执行后清理。
"""

import io
import os
import sys
import traceback
from contextlib import redirect_stdout
from pathlib import Path

from ipc_shared import SCRIPTS_DIR, ErrorCode, ok_response, err_response


def execute_script(command: dict) -> dict:
    """
    执行 execute_script 命令。

    Command fields
    --------------
    script : str   要执行的 Python 代码字符串
    id     : str   命令 ID
    """
    cmd_id = command.get("id", "unknown")
    script_content = command.get("script", "")

    if not script_content.strip():
        return err_response(cmd_id, ErrorCode.INVALID_PARAMS, "script is empty")

    # 写脚本文件（便于调试时查看）
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    script_path = SCRIPTS_DIR / f"script_{cmd_id}.py"
    try:
        script_path.write_text(script_content, encoding="utf-8")
    except Exception as e:
        return err_response(cmd_id, ErrorCode.INTERNAL_ERROR,
                            f"Failed to write script file: {e}")

    # 编译（提前捕获语法错误，报告更清晰）
    try:
        code_obj = compile(script_content, str(script_path), "exec")
    except SyntaxError as e:
        _cleanup(script_path)
        return err_response(
            cmd_id,
            ErrorCode.SCRIPT_SYNTAX_ERROR,
            f"SyntaxError at line {e.lineno}: {e.msg}",
            traceback.format_exc()
        )

    # 构建执行命名空间：注入当前 kernel 的 mdb/session
    exec_globals = {
        "__name__":    "__main__",
        "__file__":    str(script_path),
        "__builtins__": __builtins__,
    }
    try:
        # Abaqus 2021+ 用 from abaqus import mdb, session
        from abaqus import mdb, session  # noqa
        exec_globals["mdb"]     = mdb
        exec_globals["session"] = session
    except ImportError:
        pass  # 非 Abaqus 环境下测试时忽略

    # 执行，用 redirect_stdout 捕获所有 print 输出
    output_buf = io.StringIO()
    try:
        with redirect_stdout(output_buf):
            exec(code_obj, exec_globals)  # noqa: S102
    except SystemExit as e:
        _cleanup(script_path)
        return err_response(
            cmd_id,
            ErrorCode.SCRIPT_RUNTIME_ERROR,
            f"Script called sys.exit({e.code})",
        )
    except Exception as e:
        _cleanup(script_path)
        return err_response(
            cmd_id,
            ErrorCode.SCRIPT_RUNTIME_ERROR,
            str(e),
            traceback.format_exc()
        )

    _cleanup(script_path)
    output = output_buf.getvalue()
    return ok_response(cmd_id, output=output or "(Script executed successfully, no output)")


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
