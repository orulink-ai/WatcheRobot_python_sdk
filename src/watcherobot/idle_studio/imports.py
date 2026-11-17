"""Use the existing portable work format for device-owned expression assets."""
import json
import uuid

from watcherobot.runtime.daemon.maintenance.works import build_portable_work_package


def prepare_expression(value: dict):
    data = value.get("data_url")
    if not isinstance(data, str) or len(data) > 3 * 1024 * 1024:
        raise ValueError("请选择不超过 2 MB 的表情图片")
    if not data.startswith(("data:image/gif;", "data:image/png;",
                            "data:image/jpeg;", "data:image/webp;")):
        raise ValueError("支持 GIF、PNG、JPEG 和 WebP 表情")
    name = str(value.get("name") or value.get("file_name") or "我的表情")[:32]
    source_id = "custom-expression-import"
    package = build_portable_work_package({
        "workId": "idle_" + uuid.uuid4().hex[:18], "name": name, "revision": 1,
        "clips": [{"id": "expression", "kind": "expression", "resourceId": source_id,
                   "label": name, "startMs": 0, "durationMs": 1000}],
        "assets": [{"id": source_id, "kind": "expression", "name": name,
                    "fileName": str(value.get("file_name") or "expression.gif"),
                    "mimeType": data[5:data.index(";")], "dataUrl": data}],
    })
    resource = json.loads(package.files["resource_catalog.json"])["expressions"][0]["id"]
    return package, resource
