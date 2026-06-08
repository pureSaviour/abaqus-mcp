# -*- coding: utf-8 -*-
"""
abaqus_plugin/viewport_capture.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
捕获 Abaqus/CAE 视口截图，以 base64 编码返回。
"""

import base64
import time
import traceback
from pathlib import Path

from ipc_shared import SCREENSHOTS_DIR, ErrorCode, ok_response, err_response


def get_viewport_image(command: dict) -> dict:
    """
    捕获视口截图。

    Command fields
    --------------
    viewport_name : str   视口名称（默认当前活动视口）
    image_format  : str   格式：PNG / TIFF / SVG（默认 PNG）
    """
    cmd_id        = command.get("id", "unknown")
    viewport_name = command.get("viewport_name", "")
    fmt           = command.get("image_format", "PNG").upper()
    save          = command.get("save", False)

    if fmt not in ("PNG", "TIFF", "SVG"):
        fmt = "PNG"

    try:
        from abaqus import session  # noqa
    except ImportError:
        return err_response(cmd_id, ErrorCode.ABAQUS_NOT_FOUND,
                            "Not running inside Abaqus/CAE kernel")

    # 确定视口对象
    vp_name = viewport_name or getattr(session, "currentViewportName", "")
    if not vp_name or vp_name not in session.viewports:
        available = list(session.viewports.keys()) if hasattr(session, "viewports") else []
        return err_response(
            cmd_id, ErrorCode.VIEWPORT_NOT_FOUND,
            f"Viewport '{vp_name}' not found. Available: {available}"
        )

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    img_path = SCREENSHOTS_DIR / f"viewport_{int(time.time() * 1000)}.{fmt.lower()}"

    try:
        # Abaqus printToFile API
        from abaqusConstants import (  # noqa
            PNG, TIFF, SVG
        )
        fmt_const = {"PNG": PNG, "TIFF": TIFF, "SVG": SVG}.get(fmt, PNG)

        session.printToFile(
            fileName=str(img_path),
            format=fmt_const,
            canvasObjects=(session.viewports[vp_name],),
        )

        if not img_path.exists():
            return err_response(cmd_id, ErrorCode.VIEWPORT_CAPTURE_FAILED,
                                "printToFile did not create output file")

        data = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return_data = {
            "image_base64": data,
            "format": fmt.lower(),
            "viewport": vp_name,
            "data_uri": f"data:image/{fmt.lower()};base64,{data}",
        }
        if not save:
            try:
                img_path.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            return_data["save_path"] = str(img_path)

        return ok_response(cmd_id, data=return_data)

    except Exception as e:
        return err_response(cmd_id, ErrorCode.VIEWPORT_CAPTURE_FAILED,
                            str(e), traceback.format_exc())
