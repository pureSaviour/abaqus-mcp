# -*- coding: utf-8 -*-
"""
abaqus_plugin/job_manager.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
管理 Abaqus 分析作业：列出、查询状态、提交。
"""

import traceback

from ipc_shared import ErrorCode, ok_response, err_response


# Abaqus 作业状态字符串 → 人类可读描述
_STATUS_MAP = {
    "NONE":       "Not submitted",
    "QUEUED":     "Queued",
    "RUNNING":    "Running",
    "COMPLETED":  "Completed",
    "ABORTED":    "Aborted",
    "TERMINATED": "Terminated",
}


def list_jobs(command: dict) -> dict:
    """列出当前 session 中所有作业及其状态。"""
    cmd_id = command.get("id", "unknown")
    try:
        from abaqus import mdb  # noqa
    except ImportError:
        return err_response(cmd_id, ErrorCode.ABAQUS_NOT_FOUND,
                            "Not running inside Abaqus/CAE kernel")
    try:
        jobs = []
        for name, job in mdb.jobs.items():
            entry = {"name": name}
            for attr in ("status", "type", "model", "description",
                         "numCpus", "numDomains", "memory"):
                try:
                    val = getattr(job, attr, None)
                    if val is not None:
                        entry[attr] = str(val)
                except Exception:
                    pass
            # 人类可读状态
            raw_status = entry.get("status", "NONE")
            entry["status_display"] = _STATUS_MAP.get(raw_status, raw_status)
            jobs.append(entry)

        return ok_response(cmd_id, data={"jobs": jobs})

    except Exception as e:
        return err_response(cmd_id, ErrorCode.INTERNAL_ERROR,
                            str(e), traceback.format_exc())


def get_job_status(command: dict) -> dict:
    """查询单个作业的详细状态。"""
    cmd_id   = command.get("id", "unknown")
    job_name = command.get("job_name", "")

    try:
        from abaqus import mdb  # noqa
    except ImportError:
        return err_response(cmd_id, ErrorCode.ABAQUS_NOT_FOUND,
                            "Not running inside Abaqus/CAE kernel")

    if not job_name:
        return err_response(cmd_id, ErrorCode.INVALID_PARAMS, "job_name is required")

    if job_name not in mdb.jobs:
        return err_response(cmd_id, ErrorCode.JOB_NOT_FOUND,
                            f"Job '{job_name}' not found in current session")
    try:
        job = mdb.jobs[job_name]
        entry = {"name": job_name}
        for attr in ("status", "type", "model", "description",
                     "numCpus", "numDomains", "memory"):
            try:
                val = getattr(job, attr, None)
                if val is not None:
                    entry[attr] = str(val)
            except Exception:
                pass
        raw_status = entry.get("status", "NONE")
        entry["status_display"] = _STATUS_MAP.get(raw_status, raw_status)
        return ok_response(cmd_id, data=entry)

    except Exception as e:
        return err_response(cmd_id, ErrorCode.INTERNAL_ERROR,
                            str(e), traceback.format_exc())


def submit_job(command: dict) -> dict:
    """
    提交作业并等待完成（阻塞）。
    超时由 server 侧 IPC 控制（JOB_TIMEOUT = 600s），
    plugin 侧调用 job.waitForCompletion() 无限等待。
    """
    cmd_id   = command.get("id", "unknown")
    job_name = command.get("job_name", "")

    try:
        from abaqus import mdb  # noqa
    except ImportError:
        return err_response(cmd_id, ErrorCode.ABAQUS_NOT_FOUND,
                            "Not running inside Abaqus/CAE kernel")

    if not job_name:
        return err_response(cmd_id, ErrorCode.INVALID_PARAMS, "job_name is required")

    if job_name not in mdb.jobs:
        return err_response(cmd_id, ErrorCode.JOB_NOT_FOUND,
                            f"Job '{job_name}' not found in current session")

    try:
        job = mdb.jobs[job_name]
        job.submit(consistencyChecking=False)
        job.waitForCompletion()

        final_status = str(getattr(job, "status", "UNKNOWN"))
        display      = _STATUS_MAP.get(final_status, final_status)

        if final_status == "ABORTED":
            return err_response(
                cmd_id,
                ErrorCode.JOB_ABORTED,
                f"Job '{job_name}' aborted. Check the .msg file for details.",
            )

        return ok_response(cmd_id, data={
            "job":            job_name,
            "status":         final_status,
            "status_display": display,
        })

    except Exception as e:
        return err_response(
            cmd_id, ErrorCode.JOB_SUBMIT_FAILED,
            str(e), traceback.format_exc()
        )
