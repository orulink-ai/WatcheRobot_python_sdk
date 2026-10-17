import hashlib
from pathlib import Path

import httpx
import pytest

from watcherobot.management.mirror import Mirror
from watcherobot.management.releases import Artifact


def test_bad_wheel_never_published_and_partial_removed(tmp_path: Path):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"bad"))
    wheel = Artifact(
        "demo",
        "1.0",
        "demo-1.0-py3-none-any.whl",
        "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/demo.whl",
        "a" * 64,
        3,
    )
    with Mirror(transport=transport) as mirror, pytest.raises(ValueError):
        mirror.download(wheel, tmp_path)
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.whl"))


def test_cached_wheel_is_verified_before_reuse(tmp_path: Path):
    data = b"wheel"
    digest = hashlib.sha256(data).hexdigest()
    wheel = Artifact(
        "demo",
        "1.0",
        "demo-1.0-py3-none-any.whl",
        "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/demo.whl",
        digest,
        len(data),
    )
    with Mirror(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=data))
    ) as mirror:
        first = mirror.download(wheel, tmp_path)
        first.write_bytes(b"corrupt")
        assert mirror.download(wheel, tmp_path).read_bytes() == data


def test_redirect_cannot_leave_mirror(tmp_path: Path):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            302, headers={"location": "https://github.com/a.whl"}
        )
    )
    with Mirror(transport=transport) as mirror, pytest.raises(ValueError):
        mirror.fetch("https://mirrors.tuna.tsinghua.edu.cn/pypi/web/a.whl", 100)


def test_download_reports_bytes_before_completing(tmp_path: Path):
    data = b"x" * (65536 * 3)
    wheel = Artifact(
        "demo",
        "1.0",
        "demo-1.0-py3-none-any.whl",
        "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/demo.whl",
        hashlib.sha256(data).hexdigest(),
        len(data),
    )
    progress = []
    with Mirror(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=data))
    ) as mirror:
        mirror.download(wheel, tmp_path, progress=progress.append)
    assert progress[-1] == len(data)
    assert any(0 < size < len(data) for size in progress)
