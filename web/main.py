import asyncio
import os
import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

API_URL = os.getenv("API_URL", "http://localhost:8080")

app = FastAPI()
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")


async def _get(path: str, **params) -> "dict | list":
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{API_URL}{path}", params=params)
            return r.json()
    except Exception:
        return {} if path.endswith("/") else []


async def _post_json(path: str, body: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{API_URL}{path}", json=body)
            return r.json()
    except Exception:
        return {}


def _apply_speaker_names(segments: list, names: dict) -> list:
    """Substitute SPEAKER_XX labels with display names in segment text."""
    if not names:
        return segments
    out = []
    for seg in segments:
        text = seg.get("text", "")
        for speaker_id, display in names.items():
            if display:
                text = text.replace(f"【{speaker_id}】", f"【{display}】")
        out.append({**seg, "text": text})
    return out


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
    segments, speaker_data = await asyncio.gather(
        _get(f"/files/{stem}/segments", q=q),
        _get(f"/files/{stem}/speaker-names"),
    )
    segments = segments if isinstance(segments, list) else []
    speaker_data = speaker_data if isinstance(speaker_data, dict) else {}
    names = speaker_data.get("names", {})
    segments = _apply_speaker_names(segments, names)
    return templates.TemplateResponse(
        request=request,
        name="srt_viewer.html",
        context={
            "stem": stem,
            "segments": segments,
            "q": q,
            "total": len(segments),
            "speaker_data": speaker_data,
        },
    )


@app.get("/partial/srt/{stem}", response_class=HTMLResponse)
async def srt_partial(request: Request, stem: str, q: str = Query(default="")):
    """HTMX target — returns only the transcript rows."""
    segments, speaker_data = await asyncio.gather(
        _get(f"/files/{stem}/segments", q=q),
        _get(f"/files/{stem}/speaker-names"),
    )
    segments = segments if isinstance(segments, list) else []
    names = (speaker_data or {}).get("names", {}) if isinstance(speaker_data, dict) else {}
    segments = _apply_speaker_names(segments, names)
    return templates.TemplateResponse(
        request=request,
        name="partials/srt_rows.html",
        context={"segments": segments, "q": q, "total": len(segments), "stem": stem},
    )


@app.post("/srts/{stem}/speaker-names", response_class=HTMLResponse)
async def save_speaker_names(request: Request, stem: str):
    """HTMX form target — saves names, returns updated transcript partial."""
    form = await request.form()
    names = {k: str(v).strip() for k, v in form.items() if str(v).strip()}
    await _post_json(f"/files/{stem}/speaker-names", names)
    segments = await _get(f"/files/{stem}/segments")
    segments = segments if isinstance(segments, list) else []
    segments = _apply_speaker_names(segments, names)
    return templates.TemplateResponse(
        request=request,
        name="partials/srt_rows.html",
        context={"segments": segments, "q": "", "total": len(segments), "stem": stem},
    )


# ── Upload page ───────────────────────────────────────────────
@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse(request=request, name="upload.html", context={})


@app.post("/upload/init")
async def upload_init_proxy(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{API_URL}/upload/init", json=body)
        return r.json()


@app.post("/upload/complete")
async def upload_complete_proxy(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{API_URL}/upload/complete", json=body)
        return r.json()


@app.get("/partial/queue", response_class=HTMLResponse)
async def queue_partial(request: Request):
    tasks = await _get("/upload/queue")
    return templates.TemplateResponse(
        request=request,
        name="partials/queue.html",
        context={"tasks": tasks if isinstance(tasks, list) else []},
    )


@app.post("/upload/queue/{stem}/cancel", response_class=HTMLResponse)
async def cancel_upload_proxy(request: Request, stem: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.delete(f"{API_URL}/upload/queue/{stem}")
    return RedirectResponse(url="/", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}
