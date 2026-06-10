import asyncio
import os
import re
from datetime import datetime
import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

API_URL = os.getenv("API_URL", "http://localhost:8080")

app = FastAPI()
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")


def _datetimeformat(value):
    if not value:
        return "—"
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


templates.env.filters["datetimeformat"] = _datetimeformat


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


async def _get_text(path: str) -> "str | None":
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{API_URL}{path}")
            if r.status_code == 200:
                return r.text
            return None
    except Exception:
        return None


def _strip_md(text: str) -> str:
    """Extract plain-text preview from a markdown summary file."""
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("#", ">", "|", "-|-", "---")):
            continue
        if re.match(r"^[-|: ]+$", line):  # table separators / hr
            continue
        line = re.sub(r"\*{1,3}|_{1,2}|`{1,3}|~~", "", line)
        lines.append(line)
    return " ".join(lines)


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


@app.get("/partial/stats", response_class=HTMLResponse)
async def stats_partial(request: Request):
    overview, keywords = await asyncio.gather(
        _get("/stats/overview"),
        _get("/stats/keywords"),
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/stats.html",
        context={
            "overview": overview if isinstance(overview, dict) else {},
            "keywords": keywords if isinstance(keywords, list) else [],
        },
    )


@app.get("/partial/task-detail/{stem}", response_class=HTMLResponse)
async def task_detail_partial(request: Request, stem: str):
    timeline, summary_text, segments = await asyncio.gather(
        _get(f"/tasks/{stem}/timeline"),
        _get_text(f"/files/{stem}/summary"),
        _get(f"/files/{stem}/segments"),
    )
    segments = segments if isinstance(segments, list) else []
    return templates.TemplateResponse(
        request=request,
        name="partials/task_detail.html",
        context={
            "stem": stem,
            "timeline": timeline if isinstance(timeline, dict) else None,
            "summary": _strip_md(summary_text) if summary_text else None,
            "segments": segments[:3],
        },
    )


@app.get("/partial/timeline/{stem}", response_class=HTMLResponse)
async def timeline_partial(request: Request, stem: str):
    timeline = await _get(f"/tasks/{stem}/timeline")
    return templates.TemplateResponse(
        request=request,
        name="partials/timeline.html",
        context={"timeline": timeline if isinstance(timeline, dict) else None},
    )


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
    has_audio = bool(speaker_data.get("has_audio", False)) if isinstance(speaker_data, dict) else False
    return templates.TemplateResponse(
        request=request,
        name="srt_viewer.html",
        context={
            "stem": stem,
            "segments": segments,
            "q": q,
            "total": len(segments),
            "speaker_data": speaker_data,
            "has_audio": has_audio,
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
        name="partials/speaker_save_result.html",
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


@app.post("/tasks/{stem}/runs", response_class=HTMLResponse)
async def rerun_proxy(request: Request, stem: str):
    """Dashboard rerun button — proxies to API POST /tasks/{stem}/runs."""
    from html import escape
    form = await request.form()
    from_stage = form.get("from_stage") or None
    await _post_json(f"/tasks/{stem}/runs", {"from_stage": from_stage})
    s = escape(stem)
    return HTMLResponse(
        f'<div class="task-row" id="task-row-{s}">'
        f'<span class="dot dot-queued"></span>'
        f'<span class="task-stem">{s}</span>'
        f'<span class="task-stage"><span class="stage-label">queued</span></span>'
        f'</div>'
    )


@app.delete("/tasks/{stem}", response_class=HTMLResponse)
async def delete_task_web(request: Request, stem: str):
    """Dashboard cancel button — proxies to API DELETE /tasks/{stem}."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.delete(f"{API_URL}/tasks/{stem}")
    return HTMLResponse("")


@app.get("/health")
def health():
    return {"status": "ok"}
