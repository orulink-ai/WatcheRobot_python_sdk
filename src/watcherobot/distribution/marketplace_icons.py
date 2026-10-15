"""Bounded, optional icons read through the selected marketplace provider."""

from __future__ import annotations

import base64
from pathlib import PurePosixPath
import xml.etree.ElementTree as ET

from .ports import HubError, MarketplaceHubClient

MAX_ICON_BYTES = 256 * 1024
MAX_CATALOG_ICON_BYTES = 2 * 1024 * 1024


def read_marketplace_icon(
    hub: MarketplaceHubClient, *, repo_id: str, commit: str, path: str,
) -> str | None:
    """Return image data, never a provider URL or a read outside the pinned tree."""
    if (
        not path or PurePosixPath(path).is_absolute()
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(ord(char) < 32 or char in '\\:*?"<>|' for char in path)
    ):
        return None
    try:
        data = hub.read_repository_file(repo_id=repo_id, commit=commit, path=path)
    except HubError:
        return None
    if not data or len(data) > MAX_ICON_BYTES:
        return None
    mime = None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        # Decode before inspecting XML: UTF-16/32 can hide DTD tokens in bytes.
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
        if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            return None
        try:
            root = ET.fromstring(text)
            if root.tag in {"svg", "{http://www.w3.org/2000/svg}svg"}:
                mime = "image/svg+xml"
        except ET.ParseError:
            pass
    if mime is None:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
