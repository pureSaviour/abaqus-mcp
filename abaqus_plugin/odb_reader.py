# -*- coding: utf-8 -*-
"""
abaqus_plugin/odb_reader.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
ODB 文件后处理：提取场量数值结果。

这是相比两个参考项目最核心的改进——不只返回元数据，
而是直接返回 min/max/avg 等统计值，以及可选的完整节点/积分点数据。

支持的场量（field variable identifiers）：
  S    - 应力张量（含 Mises、主应力）
  E    - 应变张量
  U    - 位移向量（含 Magnitude）
  RF   - 反力
  PEEQ - 等效塑性应变
  自定义字符串（只要 ODB 里有）

注意：此文件必须用 Abaqus 内置 Python 运行（odbAccess 模块只在其中可用）。
"""

import traceback

from ipc_shared import ErrorCode, ok_response, err_response


# ---------------------------------------------------------------------------
# ODB 元数据查询
# ---------------------------------------------------------------------------

def get_odb_info(command: dict) -> dict:
    """
    打开 ODB（只读），返回结构元数据。
    不返回数值结果，只用于了解 ODB 结构。
    """
    cmd_id   = command.get("id", "unknown")
    odb_path = command.get("odb_path", "")

    if not odb_path:
        return err_response(cmd_id, ErrorCode.INVALID_PARAMS, "odb_path is required")

    try:
        from odbAccess import openOdb  # noqa
    except ImportError:
        return err_response(cmd_id, ErrorCode.ABAQUS_NOT_FOUND,
                            "odbAccess not available (not running in Abaqus Python)")

    odb = None
    try:
        odb = openOdb(path=str(odb_path), readOnly=True)
        info = {"path": str(odb_path), "steps": {}, "instances": [], "success": True}

        for step_name in odb.steps.keys():
            step = odb.steps[step_name]
            frame_count = len(step.frames)
            # 最后一帧可用的场量名称
            field_names = []
            if frame_count > 0:
                last_frame = step.frames[-1]
                field_names = list(last_frame.fieldOutputs.keys())

            info["steps"][step_name] = {
                "number":       step.number,
                "total_time":   step.totalTime,
                "frame_count":  frame_count,
                "fields":       field_names,
            }

        if hasattr(odb, "rootAssembly"):
            info["instances"] = list(odb.rootAssembly.instances.keys())

        return ok_response(cmd_id, data=info)

    except Exception as e:
        return err_response(cmd_id, ErrorCode.ODB_OPEN_FAILED,
                            str(e), traceback.format_exc())
    finally:
        if odb is not None:
            try:
                odb.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 场量数值提取（核心功能）
# ---------------------------------------------------------------------------

def query_odb_results(command: dict) -> dict:
    """
    从 ODB 提取指定场量的数值结果。

    Command fields
    --------------
    odb_path    : str   ODB 文件路径（必填）
    step_name   : str   步名（默认最后一步）
    frame_index : int   帧索引，-1 表示最后一帧（默认 -1）
    fields      : list  场量列表，如 ["S", "U", "PEEQ"]（必填）
    instance    : str   实例名（默认全部汇总）
    include_all_values : bool  是否返回每个节点/积分点的完整数据（默认 False）

    Returned data
    -------------
    {
        "step":  str,
        "frame": int,
        "time":  float,
        "results": {
            "S": {
                "components": ["S11","S22","S33","S12","S13","S23"],
                "invariants":  { "mises": {"min":..,"max":..,"avg":..},
                                 "max_principal": {..}, "min_principal": {..} },
                "stats": {
                    "S11": {"min": float, "max": float, "avg": float},
                    ...
                },
                "values": [ ... ]   # 仅 include_all_values=True 时存在
            },
            "U": {
                "components": ["U1","U2","U3"],
                "invariants":  { "magnitude": {..} },
                "stats":       { "U1": {..}, ... },
            },
            ...
        }
    }
    """
    cmd_id      = command.get("id", "unknown")
    odb_path    = command.get("odb_path", "")
    step_name   = command.get("step_name", None)
    frame_idx   = command.get("frame_index", -1)
    fields      = command.get("fields", [])
    instance    = command.get("instance", None)
    include_all = command.get("include_all_values", False)

    if not odb_path:
        return err_response(cmd_id, ErrorCode.INVALID_PARAMS, "odb_path is required")
    if not fields:
        return err_response(cmd_id, ErrorCode.INVALID_PARAMS, "fields list is required")

    try:
        from odbAccess import openOdb  # noqa
    except ImportError:
        return err_response(cmd_id, ErrorCode.ABAQUS_NOT_FOUND,
                            "odbAccess not available")

    odb = None
    try:
        odb = openOdb(path=str(odb_path), readOnly=True)

        # 确定分析步
        if step_name is None:
            step_name = list(odb.steps.keys())[-1]
        if step_name not in odb.steps:
            return err_response(cmd_id, ErrorCode.INVALID_PARAMS,
                                f"Step '{step_name}' not found. "
                                f"Available: {list(odb.steps.keys())}")

        step  = odb.steps[step_name]
        frame = step.frames[frame_idx]
        actual_frame_idx = frame_idx if frame_idx >= 0 else len(step.frames) + frame_idx

        results = {}

        for field_id in fields:
            field_id = field_id.upper()
            if field_id not in frame.fieldOutputs:
                results[field_id] = {
                    "error": f"Field '{field_id}' not in this frame. "
                             f"Available: {list(frame.fieldOutputs.keys())}"
                }
                continue

            field_output = frame.fieldOutputs[field_id]

            # 可选：按实例过滤
            if instance:
                if instance in odb.rootAssembly.instances:
                    inst_obj = odb.rootAssembly.instances[instance]
                    field_output = field_output.getSubset(region=inst_obj)
                else:
                    results[field_id] = {
                        "error": f"Instance '{instance}' not found. "
                                 f"Available: {list(odb.rootAssembly.instances.keys())}"
                    }
                    continue

            result_entry = _extract_field(field_output, field_id, include_all)
            results[field_id] = result_entry

        return ok_response(cmd_id, data={
            "odb_path":    str(odb_path),
            "step":        step_name,
            "frame_index": actual_frame_idx,
            "frame_time":  frame.frameValue,
            "results":     results,
        })

    except Exception as e:
        return err_response(cmd_id, ErrorCode.ODB_OPEN_FAILED,
                            str(e), traceback.format_exc())
    finally:
        if odb is not None:
            try:
                odb.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 内部辅助：从 FieldOutput 对象提取统计值
# ---------------------------------------------------------------------------

def _extract_field(field_output, field_id: str, include_all: bool) -> dict:
    """提取一个 FieldOutput 的统计信息。"""
    entry = {
        "description":  field_output.description,
        "type":         str(field_output.type),
        "components":   [c.name for c in field_output.componentLabels] if field_output.componentLabels else [],
        "invariants":   {},
        "stats":        {},
    }

    values = field_output.values
    if not values:
        entry["note"] = "No values found"
        return entry

    # ---------- 分量统计 ----------
    comp_labels = entry["components"]
    if comp_labels:
        # 按分量收集数据
        comp_data = {label: [] for label in comp_labels}
        for v in values:
            if hasattr(v, "data") and v.data is not None:
                data = v.data
                for i, label in enumerate(comp_labels):
                    try:
                        comp_data[label].append(float(data[i]))
                    except (IndexError, TypeError):
                        pass

        for label, vals in comp_data.items():
            if vals:
                entry["stats"][label] = _stats(vals)

    # ---------- 不变量 ----------
    # Mises 应力
    if field_id == "S":
        mises_vals = []
        max_p_vals = []
        min_p_vals = []
        for v in values:
            try:
                mises_vals.append(float(v.mises))
            except Exception:
                pass
            try:
                max_p_vals.append(float(v.maxPrincipal))
            except Exception:
                pass
            try:
                min_p_vals.append(float(v.minPrincipal))
            except Exception:
                pass
        if mises_vals:
            entry["invariants"]["mises"]         = _stats(mises_vals)
        if max_p_vals:
            entry["invariants"]["max_principal"] = _stats(max_p_vals)
        if min_p_vals:
            entry["invariants"]["min_principal"] = _stats(min_p_vals)

    # 位移幅值
    elif field_id == "U":
        mag_vals = []
        for v in values:
            try:
                mag_vals.append(float(v.magnitude))
            except Exception:
                pass
        if mag_vals:
            entry["invariants"]["magnitude"] = _stats(mag_vals)

    # 反力幅值
    elif field_id == "RF":
        mag_vals = []
        for v in values:
            try:
                mag_vals.append(float(v.magnitude))
            except Exception:
                pass
        if mag_vals:
            entry["invariants"]["magnitude"] = _stats(mag_vals)

    # ---------- 可选：完整数据点 ----------
    if include_all:
        all_vals = []
        for v in values:
            node_entry = {"node_label": v.nodeLabel if hasattr(v, "nodeLabel") else None}
            if hasattr(v, "data") and v.data is not None:
                node_entry["data"] = [float(x) for x in v.data]
            try:
                node_entry["mises"] = float(v.mises)
            except Exception:
                pass
            try:
                node_entry["magnitude"] = float(v.magnitude)
            except Exception:
                pass
            all_vals.append(node_entry)
        entry["values"] = all_vals

    return entry


def _stats(vals: list) -> dict:
    """计算列表的 min/max/avg，保留 6 位有效数字。"""
    if not vals:
        return {}
    n   = len(vals)
    mn  = min(vals)
    mx  = max(vals)
    avg = sum(vals) / n
    return {
        "min":   round(mn,  6),
        "max":   round(mx,  6),
        "avg":   round(avg, 6),
        "count": n,
    }
