"""Loopback web workbench; device operations stay in the SDK adapter."""
from __future__ import annotations

from pathlib import Path
import tarfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, FileResponse
from .official import OfficialResources
from fastapi.staticfiles import StaticFiles

from .device import DeviceReader
from .imports import prepare_expression
from .model import validate_profile, percentages, SaveSession
from watcherobot.runtime.daemon.maintenance.service import MaintenanceError


def create_app(reader: DeviceReader | None = None, official=None) -> FastAPI:
    device = reader or DeviceReader()
    resources = official or OfficialResources()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        if host.split(":")[0] not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"http://{host}":
            return JSONResponse({"detail": "请求来源不匹配"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get('/api/official/preview')
    def official_preview(resource: str, sha256: str | None = None):
        try:
            return resources.preview(resource, sha256)
        except (ValueError, OSError, KeyError, tarfile.TarError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/official/companion')
    def official_companion(resource: str, kind: str, sha256: str | None = None):
        try:
            result = resources.companion(resource, kind, sha256)
            return Response(result, media_type='audio/wav') if kind == 'sound' else result
        except (ValueError, OSError, KeyError, tarfile.TarError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/official/sheet/{digest}')
    def official_sheet(digest: str):
        try:
            path = resources.sheet(digest)
            if not path.is_file():
                raise ValueError('请先加载动画预览')
            return FileResponse(path, media_type='image/png')
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/ports")
    def ports():
        return device.ports()

    @app.get("/api/device")
    def read_device(port: str):
        try:
            return device.read(port)
        except Exception as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/preview")
    def preview(port: str, resource: str, device_id: str | None = None, request_id: str | None = None):
        try:
            options = {'device_id': device_id}
            if request_id is not None:
                options['request_id'] = request_id
            return Response(device.preview(port, resource, **options), media_type="image/gif")
        except (ValueError, OSError, MaintenanceError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/preview-progress')
    def preview_progress(request_id: str):
        return device.preview_progress(request_id)

    @app.post("/api/save")
    def save(value: dict):
        try:
            identity, revision = value.get("device_id"), value.get("revision")
            if not isinstance(identity, str) or not identity or type(revision) is not int or revision < 0:
                raise ValueError("请先重新读取设备")
            return device.save(value.get("port", ""), SaveSession(identity, revision), value.get("profile"))
        except (ValueError, OSError, MaintenanceError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/validate")
    def validate(value: dict):
        try:
            profile = validate_profile(value)
            return {"profile": profile, "percentages": percentages(profile)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/import-expression")
    def import_expression(value: dict):
        try:
            identity, revision = value.get("device_id"), value.get("revision")
            if not isinstance(identity, str) or not identity or type(revision) is not int or revision < 0:
                raise ValueError("请先重新读取设备")
            package, resource = prepare_expression(value)
            return device.import_expression(value.get("port", ""), SaveSession(identity, revision),
                                            package, resource, value.get("request_id"))
        except (ValueError, OSError, MaintenanceError) as exc:
            raise HTTPException(409, str(exc)) from exc

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web")
    return app
