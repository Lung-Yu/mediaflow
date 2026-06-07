import os
import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

API_URL = os.getenv("API_URL", "http://localhost:8080")

app = FastAPI()
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")


async def _get(path: str, **params) -> dict | list:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{API_URL}{path}", params=params)
            return r.json()
    except Exception:
        return {} if path.endswith("/") else []


# ── Dashboard ────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    data = await _get("/status/")
    return templates.TemplateResponse(request=request, name="dashboard.html", context=data or {})


@app.get("/partial/status", response_class=HTMLResponse)
async def status_partial(request: Request):
    data = await _get("/status/")
    return templates.TemplateResponse(request=request, name="partials/status.html", context=data or {})


# ── SRT list ─────────────────────────────────────────────────
@app.get("/srts", response_class=HTMLResponse)
async def srt_list(request: Request):
    files = await _get("/files/")
    return templates.TemplateResponse(request=request, name="srts.html", context={"files": files})


# ── SRT viewer ────────────────────────────────────────────────
@app.get("/srts/{stem}", response_class=HTMLResponse)
async def srt_viewer(request: Request, stem: str, q: str = ""):
    segments = await _get(f"/files/{stem}/segments", q=q)
    return templates.TemplateResponse(
        request=request,
        name="srt_viewer.html",
        context={"stem": stem, "segments": segments, "q": q, "total": len(segments)},
    )


@app.get("/partial/srt/{stem}", response_class=HTMLResponse)
async def srt_partial(request: Request, stem: str, q: str = Query(default="")):
    """HTMX target — returns only the transcript rows."""
    segments = await _get(f"/files/{stem}/segments", q=q)
    return templates.TemplateResponse(
        request=request,
        name="partials/srt_rows.html",
        context={"segments": segments, "q": q, "total": len(segments), "stem": stem},
    )


@app.get("/health")
def health():
    return {"status": "ok"}
