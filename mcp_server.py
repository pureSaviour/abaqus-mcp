# -*- coding: utf-8 -*-
"""
mcp_server.py
~~~~~~~~~~~~~
Abaqus MCP Server —— 外部进程，通过 stdio 与 MCP 客户端（Claude/Cursor 等）通信。
通过 File IPC 与 Abaqus/CAE 内部的 plugin_main.py 交互。

工具分两层：
  Layer A  通用工具（透传到 plugin）
    - check_connection
    - execute_script
    - get_model_info
    - list_jobs / get_job_status / submit_job
    - get_odb_info / query_odb_results
    - get_viewport_image

  Layer B  高层语义工具（阶段 3 扩展，此处预留接口）
    - run_tension_simulation（待实现）
"""

import json
from email.message import Message

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import UserMessage

from ipc_client import send_command, check_plugin_alive
from ipc_shared import CmdType, DEFAULT_TIMEOUT, JOB_TIMEOUT, ODB_TIMEOUT, VIEWPORT_TIMEOUT

# ---------------------------------------------------------------------------
# FastMCP 实例
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "abaqus-mcp",
    instructions=(
        "This server controls Abaqus/CAE for FEA modeling and simulation. "
        "Always call check_connection first to verify the plugin is running. "
        "Use execute_script for custom scripting, or the high-level tools for "
        "standard workflows. After submitting a job, use query_odb_results to "
        "extract stress/displacement results directly as structured data."
    ),
)


# ---------------------------------------------------------------------------
# 辅助：统一格式化响应给 LLM
# ---------------------------------------------------------------------------

def _format_result(result: dict) -> str:
    """
    把 plugin 响应格式化为对 LLM 友好的字符串。
    成功时返回 data/output，失败时返回结构化错误。
    """
    if result.get("success"):
        data   = result.get("data")
        output = result.get("output", "")
        if data is not None and output:
            return f"{output}\n\n{json.dumps(data, indent=2, ensure_ascii=False)}"
        if data is not None:
            return json.dumps(data, indent=2, ensure_ascii=False)
        return output or "(OK, no output)"
    else:
        error_code = result.get("error_code", "UNKNOWN")
        message    = result.get("error", "Unknown error")
        tb         = result.get("traceback", "")
        lines = [f"[ERROR {error_code}] {message}"]
        if tb:
            lines.append(f"\nTraceback:\n{tb}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layer A：通用工具
# ---------------------------------------------------------------------------

@mcp.tool()
def check_connection() -> str:
    """
    Check if the Abaqus/CAE plugin is loaded and responding.
    Always call this first to verify the connection before other operations.
    Returns plugin version, uptime, and connection status.
    """
    alive, message = check_plugin_alive()
    if alive:
        return f"✓ Connected. {message}"
    else:
        return f"✗ Not connected. {message}"


@mcp.tool()
def execute_script(script: str) -> str:
    """
    Execute a Python script inside the running Abaqus/CAE kernel.

    The script has full access to `mdb` and `session` objects.
    Use `print()` to return output. The output is captured and returned.

    Parameters
    ----------
    script : str
        Complete Python script using Abaqus Scripting Interface (ASI) commands.

    Returns the captured stdout output, or an error message with traceback.

    Examples
    --------
    - Query model names: `print(list(mdb.models.keys()))`
    - Create a material: `mdb.models['Model-1'].Material(name='Steel')`
    """
    result = send_command(CmdType.EXECUTE_SCRIPT, script=script)
    return _format_result(result)


@mcp.tool()
def get_model_info() -> str:
    """
    Get detailed information about all models in the current Abaqus session.

    Returns a JSON summary including:
    - Parts, materials, sections, steps
    - Loads and boundary conditions (with types)
    - Assembly instances
    - Mesh summary (node/element counts per part)
    - Defined jobs and their status
    - Current working directory
    """
    result = send_command(CmdType.GET_MODEL_INFO)
    return _format_result(result)


@mcp.tool()
def list_jobs() -> str:
    """
    List all analysis jobs defined in the current Abaqus session.
    Returns job names, models, status, and CPU/memory settings.
    """
    result = send_command(CmdType.LIST_JOBS)
    return _format_result(result)


@mcp.tool()
def get_job_status(job_name: str) -> str:
    """
    Get the current status of a specific Abaqus analysis job.

    Parameters
    ----------
    job_name : str
        Name of the job as defined in mdb.jobs.
    """
    result = send_command(CmdType.GET_JOB_STATUS, job_name=job_name)
    return _format_result(result)


@mcp.tool()
def submit_job(job_name: str) -> str:
    """
    Submit an Abaqus analysis job and wait for it to complete.

    The job must already be defined in the current session (use execute_script
    or a high-level tool to create it first).

    Parameters
    ----------
    job_name : str
        Name of the job to submit.

    Returns the final job status (COMPLETED or ABORTED with details).
    Note: This call blocks until the job finishes (up to 10 minutes).
    """
    result = send_command(CmdType.SUBMIT_JOB, timeout=JOB_TIMEOUT, job_name=job_name)
    return _format_result(result)


@mcp.tool()
def get_odb_info(odb_path: str) -> str:
    """
    Open an ODB file (read-only) and return its structure metadata.

    Shows available steps, frame counts, total time, and which field
    variables (S, U, E, PEEQ, etc.) are available in the last frame.

    Parameters
    ----------
    odb_path : str
        Full path to the .odb file. Use forward slashes or double backslashes.
    """
    result = send_command(CmdType.GET_ODB_INFO, timeout=ODB_TIMEOUT, odb_path=odb_path)
    return _format_result(result)


@mcp.tool()
def query_odb_results(
    odb_path: str,
    fields: list[str],
    step_name: str = "",
    frame_index: int = -1,
    instance: str = "",
    include_all_values: bool = False,
) -> str:
    """
    Extract numerical results from an ODB file.

    Returns min/max/avg statistics for each requested field variable.
    This is the primary tool for post-processing simulation results.

    Parameters
    ----------
    odb_path : str
        Full path to the .odb file.
    fields : list[str]
        Field variables to extract, e.g. ["S", "U", "PEEQ"].
        Common values:
          S    - Stress tensor (includes Mises, max/min principal)
          E    - Strain tensor
          U    - Displacement vector (includes magnitude)
          RF   - Reaction forces
          PEEQ - Equivalent plastic strain
    step_name : str
        Step to query (default: last step).
    frame_index : int
        Frame index, -1 = last frame (default).
    instance : str
        Restrict to a specific assembly instance (default: all).
    include_all_values : bool
        If True, include per-node data in the response (can be large).
        Default: False (statistics only).

    Returns structured JSON with statistics per field and component.
    """
    kwargs = {
        "odb_path":          odb_path,
        "fields":            fields,
        "frame_index":       frame_index,
        "include_all_values": include_all_values,
    }
    if step_name:
        kwargs["step_name"] = step_name
    if instance:
        kwargs["instance"] = instance

    result = send_command(CmdType.QUERY_ODB_RESULTS, timeout=ODB_TIMEOUT, **kwargs)
    return _format_result(result)


@mcp.tool()
def get_viewport_image(
    viewport_name: str = "",
    image_format: str = "PNG",
    save:bool = False
) -> str:
    """
    Capture a screenshot of an Abaqus/CAE viewport.

    Returns a data URI (data:image/png;base64,...) that can be displayed inline.

    Parameters
    ----------
    viewport_name : str
        Name of the viewport to capture (default: current active viewport).
    image_format : str
        Output format: PNG (default), TIFF, or SVG.
    save : bool
        To save the captured image (default: False).Usually can be set true when user want to trace the workflow
    """
    kwargs: dict = {"image_format": image_format.upper(), "save": save}
    if viewport_name:
        kwargs["viewport_name"] = viewport_name

    result = send_command(
        CmdType.GET_VIEWPORT_IMAGE, timeout=VIEWPORT_TIMEOUT, **kwargs
    )
    if result.get("success"):
        data = result.get("data", {})
        return data.get("data_uri", "") or _format_result(result)
    return _format_result(result)


# ---------------------------------------------------------------------------
# Layer B 占位（阶段 3 实现）
# ---------------------------------------------------------------------------

@mcp.tool()
def run_tension_simulation(
    length: float,
    width: float,
    height: float,
    elastic_modulus: float,
    poisson_ratio: float,
    force: float,
    mesh_size: float = 0.0,
    model_name: str = "TensionModel",
) -> str:
    """
    [PHASE 3 - NOT YET IMPLEMENTED]
    High-level tool: create a tensile specimen, mesh it, apply boundary
    conditions, submit the job, and return stress/displacement results.

    Parameters
    ----------
    length, width, height : float   Specimen dimensions (mm)
    elastic_modulus       : float   Young's modulus (MPa)
    poisson_ratio         : float   Poisson's ratio
    force                 : float   Applied tensile force (N)
    mesh_size             : float   Global mesh seed size (0 = auto)
    model_name            : str     Model name in Abaqus session
    """
    return (
        "[NOT YET IMPLEMENTED] run_tension_simulation is planned for Phase 3.\n"
        "Use execute_script with manual ASI commands for now, or wait for the next update."
    )


# ---------------------------------------------------------------------------
# 系统提示词：指导 LLM 如何使用这个 server
# ---------------------------------------------------------------------------

@mcp.prompt()
def abaqus_workflow_guide() -> str:
    """
    Recommended workflow guide for using this Abaqus MCP server.
    Read this before starting a simulation task.
    """
    return """# Abaqus MCP Server — Workflow Guide

## Connection
Always start with `check_connection` to verify the plugin is loaded and running.
If not connected, instruct the user to:
1. Open Abaqus/CAE
2. File → Run Script → `abaqus_plugin/plugin_main.py`
3. In the Abaqus console, run: `mcp_start()`

## Typical Simulation Workflow

### Option A: High-level tools (Phase 3, coming soon)
```
check_connection → run_tension_simulation → query_odb_results
```

### Option B: Script-based workflow (available now)
```
check_connection
  → execute_script (build model)
  → execute_script (create job)
  → submit_job
  → query_odb_results (extract S, U, PEEQ from .odb)
  → get_viewport_image (optional screenshot)
```

## Error Handling
- `TIMEOUT`: Plugin is not consuming commands. Check `mcp_status()` in Abaqus.
- `JOB_ABORTED`: Check the `.msg` file in the working directory for solver errors.
- `ODB_FIELD_MISSING`: Use `get_odb_info` first to see what fields are available.
- `SCRIPT_RUNTIME_ERROR`: The Abaqus Python traceback is included; fix the script and retry.

## Units
Abaqus has no built-in unit system. Be explicit and consistent:
- Recommended for structural: mm, N, MPa (N/mm²), tonne (mass)
- Document assumed units in your workflow comments.

## Getting Numerical Results
After a job completes, always use `query_odb_results` instead of execute_script
for post-processing. It returns structured JSON with min/max/avg statistics,
which is far more useful than parsing print() output.

Example call:
```
query_odb_results(
    odb_path="C:/work/TensionJob.odb",
    fields=["S", "U"],
    frame_index=-1
)
```
"""

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
