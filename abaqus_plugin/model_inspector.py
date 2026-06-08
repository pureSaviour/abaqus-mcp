# -*- coding: utf-8 -*-
"""
abaqus_plugin/model_inspector.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
查询当前 Abaqus session 中的模型信息。
返回结构化 JSON，供 LLM 理解当前建模状态。
"""

import os
import traceback

from ipc_shared import ErrorCode, ok_response, err_response


def get_model_info(command: dict) -> dict:
    """
    返回当前 session 中所有模型的详细信息。

    Returned data structure
    -----------------------
    {
        "models": [
            {
                "name": str,
                "parts": [str, ...],
                "materials": [str, ...],
                "sections": [str, ...],
                "steps": [str, ...],
                "loads": { name: type, ... },
                "boundary_conditions": { name: type, ... },
                "interactions": [str, ...],
                "assembly_instances": [str, ...],
                "mesh_summary": { part_name: { nodes: int, elements: int }, ... }
            }
        ],
        "jobs": [ { "name": str, "status": str, "model": str }, ... ],
        "working_directory": str,
        "current_viewport": str
    }
    """
    cmd_id = command.get("id", "unknown")
    try:
        from abaqus import mdb, session  # noqa
    except ImportError:
        return err_response(cmd_id, ErrorCode.ABAQUS_NOT_FOUND,
                            "Not running inside Abaqus/CAE kernel")

    try:
        info = {
            "models":            [],
            "jobs":              [],
            "working_directory": os.getcwd(),
            "current_viewport":  getattr(session, "currentViewportName", ""),
        }

        # ---------- 模型 ----------
        for model_name in mdb.models.keys():
            m = mdb.models[model_name]
            model_data = {
                "name":                 model_name,
                "parts":                list(m.parts.keys()) if hasattr(m, "parts") else [],
                "materials":            list(m.materials.keys()) if hasattr(m, "materials") else [],
                "sections":             list(m.sections.keys()) if hasattr(m, "sections") else [],
                "steps":                list(m.steps.keys()) if hasattr(m, "steps") else [],
                "loads":                {},
                "boundary_conditions":  {},
                "interactions":         list(m.interactions.keys()) if hasattr(m, "interactions") else [],
                "assembly_instances":   [],
                "mesh_summary":         {},
            }

            # loads：记录名称 → 类型
            if hasattr(m, "loads"):
                for name, load in m.loads.items():
                    model_data["loads"][name] = type(load).__name__

            # boundary conditions
            if hasattr(m, "boundaryConditions"):
                for name, bc in m.boundaryConditions.items():
                    model_data["boundary_conditions"][name] = type(bc).__name__

            # assembly instances
            if hasattr(m, "rootAssembly") and m.rootAssembly:
                ra = m.rootAssembly
                if hasattr(ra, "instances"):
                    model_data["assembly_instances"] = list(ra.instances.keys())

            # mesh summary（节点数、单元数）
            if hasattr(m, "parts"):
                for pname, part in m.parts.items():
                    try:
                        nodes    = len(part.nodes)    if hasattr(part, "nodes")    else 0
                        elements = len(part.elements) if hasattr(part, "elements") else 0
                        if nodes > 0 or elements > 0:
                            model_data["mesh_summary"][pname] = {
                                "nodes":    nodes,
                                "elements": elements,
                            }
                    except Exception:
                        pass

            info["models"].append(model_data)

        # ---------- 作业 ----------
        if hasattr(mdb, "jobs"):
            for jname, job in mdb.jobs.items():
                job_entry = {"name": jname}
                for attr in ("status", "type", "model", "description"):
                    try:
                        val = getattr(job, attr, None)
                        if val is not None:
                            job_entry[attr] = str(val)
                    except Exception:
                        pass
                info["jobs"].append(job_entry)

        return ok_response(cmd_id, data=info)

    except Exception as e:
        return err_response(
            cmd_id, ErrorCode.INTERNAL_ERROR,
            str(e), traceback.format_exc()
        )
