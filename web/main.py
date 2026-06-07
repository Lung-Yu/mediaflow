import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

API_URL = os.getenv("API_URL", "http://localhost:8080")

app = FastAPI()
app.mount("/static", StaticFiles(directory="web/static"), name="static")
templates = Jinja2Templates(directory="web/templates")


async def _fetch_status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{API_URL}/status/")
            return r.json()
    except Exception:
        return {"processing": [], "queue": [], "recent": [], "failed": []}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    data = await _fetch_status()
    return templates.TemplateResponse("dashboard.html", {"request": request, **data})


@app.get("/partial/status", response_class=HTMLResponse)
async def status_partial(request: Request):
    """HTMX polling target — returns only the inner content, not the full page."""
    data = await _fetch_status()
    return templates.TemplateResponse("partials/status.html", {"request": request, **data})


@app.get("/health")
def health():
    return {"status": "ok"}
