"""Loopback Application adapter. Planning is read-only; device execution is not enabled."""
from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from behavior_driver import BehaviorDriver, BehaviorRequest


def create_web_app(web_root: Path, *, run_credential: str | None = None) -> FastAPI:
    credential = run_credential or secrets.token_urlsafe(32)
    document = (web_root / "joyinside-preview.html").read_text(encoding="utf-8")
    bootstrap = json.dumps({"apiBase": "/api/behavior", "runCredential": credential})
    document = document.replace("</head>", f'<script id="behavior-lab-config" type="application/json">{bootstrap}</script></head>')
    driver = BehaviorDriver()
    app = FastAPI(title="WatcheRobot Speaking Motion Lab", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def require_local_run(request: Request) -> None:
        supplied = request.headers.get("x-behavior-run", "")
        if not secrets.compare_digest(supplied, credential):
            raise HTTPException(403, detail="Application run credential required")
        origin = request.headers.get("origin")
        if origin is not None and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, detail="Cross-origin requests are not accepted")

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse("/joyinside-preview.html")

    @app.get("/joyinside-preview.html")
    async def index() -> HTMLResponse:
        return HTMLResponse(document, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/status")
    async def status() -> dict:
        return {"appId": "com.orulink.speaking_motion_lab", "mode": "preview", "deviceExecution": False,
                "driverSchema": "watche.behavior.request.v1"}

    @app.post("/api/behavior/plan", dependencies=[Depends(require_local_run)])
    async def plan(body: dict) -> dict:
        try:
            request = BehaviorRequest.from_dict(body)
        except ValueError as error:
            raise HTTPException(422, detail={"code": "invalid_behavior_request", "message": str(error)}) from error
        result = await asyncio.to_thread(driver.plan, request)
        return result.to_dict()

    # Serve only the prebuilt web directory, never Application Python sources or credentials.
    app.mount("/", StaticFiles(directory=web_root), name="web")
    return app
