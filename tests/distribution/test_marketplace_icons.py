from __future__ import annotations

import base64

import pytest

from watcherobot.distribution.marketplace_icons import read_marketplace_icon
from watcherobot.distribution.ports import HubFileNotFound

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle r="4"/></svg>'

class Hub:
    def __init__(self, payload=SVG):
        self.payload = payload
        self.calls = []

    def read_repository_file(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_reads_icon_from_exact_reviewed_revision():
    hub = Hub()
    result = read_marketplace_icon(hub, repo_id="alice/app", commit="a" * 40, path="assets/icon.svg")
    assert result == "data:image/svg+xml;base64," + base64.b64encode(SVG).decode()
    assert hub.calls == [{"repo_id": "alice/app", "commit": "a" * 40, "path": "assets/icon.svg"}]


@pytest.mark.parametrize("path", ["", "../icon.svg", "/icon.svg", "https://evil/icon.svg", "a\\icon.svg", "a/../icon.svg"])
def test_invalid_path_is_not_fetched(path):
    hub = Hub()
    assert read_marketplace_icon(hub, repo_id="alice/app", commit="a" * 40, path=path) is None
    assert hub.calls == []


@pytest.mark.parametrize("payload", [b"<html>error</html>", b"x" * (256 * 1024 + 1), b'<!DOCTYPE svg [<!ENTITY x "oops">]><svg/>', HubFileNotFound("missing")], ids=["html", "oversized", "entity", "missing"])
def test_missing_invalid_or_oversized_icon_is_optional(payload):
    assert read_marketplace_icon(Hub(payload), repo_id="alice/app", commit="a" * 40, path="icon.svg") is None
