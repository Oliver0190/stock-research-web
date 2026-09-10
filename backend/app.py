from contextlib import asynccontextmanager
from datetime import date, datetime
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.market import HK
from backend.service import ResearchService
from backend.settings import ROOT


class RefreshRequest(BaseModel):
    symbol: str | None = Field(default=None, pattern=r"^\d{5}$")


class NoteRequest(BaseModel):
    note: str = Field(max_length=10000)


class ReadRequest(BaseModel):
    through: datetime
    report_ids: list[str] = Field(max_length=400)


def create_app(service=None, schedule=True):
    service = service or ResearchService()

    @asynccontextmanager
    async def lifespan(app):
        if schedule and service.auto_refresh:
            threading.Thread(target=service.scheduler, daemon=True, name="stock-scheduler").start()
        yield
        service.close()

    app = FastAPI(title="港股观察", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请从本机网站操作"}, status_code=403)
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"detail": "需要 JSON 请求"}, status_code=415)
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "无效的请求长度"}, status_code=400)
            if length > 65536:
                return JSONResponse({"detail": "内容过长"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def known(symbol):
        if symbol not in service.items:
            raise HTTPException(404, "股票不在关注列表中")

    @app.get("/api/overview")
    def overview(day: date | None = None):
        return service.overview(day.isoformat() if day else None)

    @app.get("/api/stocks/{symbol}")
    def stock(symbol: str, day: date | None = None):
        known(symbol)
        return service.detail(symbol, day.isoformat() if day else None)

    @app.get("/api/status")
    def status():
        return {"update": service.status(), "stocks": service.store.statuses()}

    @app.post("/api/refresh", status_code=202)
    def refresh(body: RefreshRequest):
        if body.symbol:
            known(body.symbol)
        started = service.start([body.symbol] if body.symbol else None)
        return {"started": started, "update": service.status()}

    @app.put("/api/stocks/{symbol}/note")
    def note(symbol: str, body: NoteRequest):
        known(symbol)
        service.store.save_note(symbol, body.note, datetime.now(HK).isoformat(timespec="seconds"))
        return service.store.profile(symbol)

    @app.post("/api/stocks/{symbol}/read")
    def read(symbol: str, body: ReadRequest):
        known(symbol)
        if body.through.tzinfo is None:
            raise HTTPException(422, "时间必须含时区")
        through = body.through.astimezone(HK).isoformat(timespec="seconds")
        service.store.mark_read(symbol, datetime.now(HK).isoformat(timespec="seconds"), through, body.report_ids)
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(ROOT / "frontend" / "index.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")
    return app
