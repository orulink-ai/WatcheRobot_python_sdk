import base64
import json
from io import BytesIO

import pytest
from PIL import Image

from watcherobot.idle_studio.imports import prepare_expression


def test_import_contains_real_device_animation_and_unique_work():
    source = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(source, format="GIF")
    value = {"name": "我的表情", "file_name": "face.gif",
             "data_url": "data:image/gif;base64," + base64.b64encode(source.getvalue()).decode()}
    package, resource = prepare_expression(value)
    other, _ = prepare_expression(value)
    assert package.work_id != other.work_id
    catalog = json.loads(package.files["resource_catalog.json"])
    assert catalog["expressions"][0]["id"] == resource
    assert resource.startswith("we_")
    assert any(data.startswith(b"ANPK") for data in package.files.values())
    assert not package.manifest["dependencies"]


@pytest.mark.parametrize("value", [{}, {"data_url": "https://example.com/face.gif"},
                                      {"data_url": "data:image/gif;base64,broken"}])
def test_import_rejects_invalid_media(value):
    with pytest.raises(ValueError):
        prepare_expression(value)
