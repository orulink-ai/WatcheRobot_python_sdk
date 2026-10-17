"""Bounded HTTPS mirror access; downloaded metadata is never executed."""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin
from zipfile import ZipFile

import httpx
from packaging.utils import parse_wheel_filename
from packaging.version import Version

from .releases import Artifact, Catalog, validate_artifact_url, verify_catalog

CATALOG_INDEX = (
    "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/watcherobot-desktop-release/"
)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag == "a" and "data-yanked" not in fields and fields.get("href"):
            self.links.append(str(fields["href"]))


class Mirror:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.client = httpx.Client(
            timeout=httpx.Timeout(60, connect=15),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def __enter__(self) -> Mirror:  # noqa: PYI034 -- support Python 3.10 without another dependency
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def transfer(
        self,
        url: str,
        limit: int,
        write: Callable[[bytes], object],
        checkpoint: Callable[[], None] = lambda: None,
    ) -> int:
        for _ in range(5):
            validate_artifact_url(url)
            checkpoint()
            with self.client.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                size = 0
                for chunk in response.iter_bytes(65536):
                    checkpoint()
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("Mirror response exceeds declared size")
                    write(chunk)
                return size
        raise ValueError("Too many mirror redirects")

    def fetch(self, url: str, limit: int) -> bytes:
        stream = io.BytesIO()
        self.transfer(url, limit, stream.write)
        return stream.getvalue()

    def catalog(self, public_keys: dict[str, str]) -> tuple[Catalog, bytes]:
        if not public_keys:
            raise ValueError("SDK release signing keys are not configured")
        parser = _Links()
        parser.feed(self.fetch(CATALOG_INDEX, 1024 * 1024).decode("utf-8"))
        candidates: list[tuple[Version, str]] = []
        for link in parser.links:
            url, _ = urldefrag(urljoin(CATALOG_INDEX, link))
            filename = url.rsplit("/", 1)[-1]
            try:
                name, version, _, tags = parse_wheel_filename(filename)
            except ValueError:
                continue
            if (
                name == "watcherobot-desktop-release"
                and not version.is_prerelease
                and not version.is_devrelease
                and any(str(t) == "py3-none-any" for t in tags)
            ):
                candidates.append((version, validate_artifact_url(url)))
        if not candidates:
            raise ValueError("No stable SDK release catalog is mirrored yet")
        # The latest mirrored metadata package is authoritative. Never silently
        # downgrade to older metadata when signature or content is invalid.
        _, url = max(candidates)
        with ZipFile(io.BytesIO(self.fetch(url, 4 * 1024 * 1024))) as archive:
            info = archive.getinfo("watcherobot_desktop_release/catalog.json")
            if info.file_size > 2 * 1024 * 1024:
                raise ValueError("SDK catalog exceeds limit")
            blob = archive.read(info)
        return verify_catalog(blob, public_keys), blob

    def download(
        self,
        wheel: Artifact,
        root: Path,
        checkpoint: Callable[[], None] = lambda: None,
        *,
        progress: Callable[[int], None] = lambda size: None,
    ) -> Path:
        directory = root / wheel.sha256
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / wheel.filename
        if target.is_symlink():
            raise ValueError("Artifact cache must not contain links")
        if target.is_file() and target.stat().st_size == wheel.size:
            with target.open("rb") as cached_stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: cached_stream.read(65536), b""):
                    checkpoint()
                    digest.update(chunk)
            if digest.hexdigest() == wheel.sha256:
                progress(wheel.size)
                return target
        temporary = directory / (uuid.uuid4().hex + ".part")
        digest = hashlib.sha256()
        downloaded = 0
        try:
            with temporary.open("xb") as stream:

                def write(chunk: bytes) -> None:
                    nonlocal downloaded
                    digest.update(chunk)
                    stream.write(chunk)
                    downloaded += len(chunk)
                    progress(downloaded)

                size = self.transfer(wheel.url, wheel.size, write, checkpoint)
                stream.flush()
                os.fsync(stream.fileno())
            if size != wheel.size or digest.hexdigest() != wheel.sha256:
                raise ValueError("SDK wheel integrity verification failed")
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)
