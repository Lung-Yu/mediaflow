# React Frontend Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Jinja2 + HTMX web layer (`web/`) with a React 18 SPA served by nginx, preserving all existing features and adding SSE real-time updates, drag-and-drop upload, and inline SRT editing.

**Architecture:** React SPA lives in `frontend/`; nginx serves the static build on port 3000 and proxies `/api/*` to `api:8080`; the existing API is unchanged except for two new endpoints (SSE stream + SRT save). Vite dev server proxies `/api` locally during development.

**Tech Stack:** React 18, TypeScript, Vite 5, Tailwind CSS 3, shadcn/ui (Radix UI), TanStack Query v5, React Router v6, nginx Alpine, Vitest

## Global Constraints

- All React source lives under `frontend/src/`; no code outside `frontend/` except the two new API endpoints
- API base path in the browser is `/api` (nginx strips the prefix before forwarding to `api:8080`)
- The `web/` directory is kept but the Docker `web` service is replaced — do not delete `web/` (keep for reference)
- Tailwind dark theme: background `neutral-950`, card `neutral-900`, border `neutral-800`, accent `purple-400`
- shadcn/ui components installed via `npx shadcn@latest add <component>` — do not hand-write Radix primitives
- All text labels matching current UI must be in 繁體中文 where the original used Chinese
- TypeScript strict mode (`"strict": true`); no `any` except in Vitest mocks
- Commits: `feat(frontend): …` prefix for frontend work, `feat(api): …` for API additions
- Do not push to remote

---

## File Map

### New files — `frontend/`
```
frontend/
  package.json
  tsconfig.json
  tsconfig.node.json
  vite.config.ts
  tailwind.config.js
  postcss.config.js
  components.json            ← shadcn/ui config
  index.html
  Dockerfile                 ← multi-stage: node builder → nginx:alpine
  nginx.conf
  src/
    main.tsx
    App.tsx
    api/
      types.ts               ← all TypeScript interfaces for API responses
      client.ts              ← typed fetch wrappers for every endpoint
    hooks/
      useSSE.ts              ← EventSource hook that feeds TanStack Query cache
    pages/
      Dashboard.tsx
      Transcripts.tsx
      SrtViewer.tsx
      Upload.tsx
    components/
      Layout.tsx             ← header + nav + live clock + Outlet
      StatusBar.tsx          ← 4-cell grid: processing/queued/completed/failed
      JobList.tsx            ← now-processing / queue / recent / failed sections
      TaskAccordion.tsx      ← expandable completed task (summary + timeline + rerun)
      StatsPanel.tsx         ← speaker bar chart + keyword table
      AudioPlayer.tsx        ← sticky bar, seek track, click-to-seek
      SrtSegmentList.tsx     ← segment rows with search highlight + click-to-seek
      SpeakerPanel.tsx       ← speaker label editor (collapsible)
      TimelinePanel.tsx      ← stage duration bar chart (collapsible)
      SrtEditor.tsx          ← toggle edit mode, inline textarea per segment, save
      DropZone.tsx           ← drag-and-drop + file-picker
      UploadProgress.tsx     ← per-file progress bar + status label
```

### New API files
```
api/routes/stream.py        ← GET /events/stream (SSE, pushes status every 5s)
```

### Modified existing files
```
api/routes/files.py         ← add PUT /{stem}/srt
api/main.py                 ← register stream.router
docker-compose.yml          ← replace web service build context
```

---

### Task 1: Project scaffold + Docker

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tailwind.config.js`
- Create: `frontend/postcss.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/nginx.conf`
- Create: `frontend/Dockerfile`
- Modify: `docker-compose.yml` (web service)

**Interfaces:**
- Produces: working Vite dev server on `npm run dev`, working `npm run build`, working Docker image

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "mediaflow-frontend",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.25.1",
    "@tanstack/react-query": "^5.51.15",
    "clsx": "^2.1.1",
    "tailwind-merge": "^2.4.0",
    "class-variance-authority": "^0.7.0",
    "@radix-ui/react-accordion": "^1.2.0",
    "@radix-ui/react-collapsible": "^1.1.0",
    "@radix-ui/react-dialog": "^1.1.1",
    "@radix-ui/react-label": "^2.1.0",
    "@radix-ui/react-separator": "^1.1.0",
    "@radix-ui/react-slot": "^1.1.0",
    "lucide-react": "^0.414.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.40",
    "tailwindcss": "^3.4.7",
    "typescript": "^5.5.3",
    "vite": "^5.3.5",
    "vitest": "^2.0.5",
    "@testing-library/react": "^16.0.0",
    "@testing-library/user-event": "^14.5.2",
    "jsdom": "^24.1.1"
  }
}
```

- [ ] **Step 2: Create `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 3: Create `frontend/tsconfig.node.json`**

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 4: Create `frontend/vite.config.ts`**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: [],
  },
})
```

- [ ] **Step 5: Create `frontend/tailwind.config.js`**

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['JetBrains Mono', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
```

- [ ] **Step 6: Create `frontend/postcss.config.js`**

```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 7: Create `frontend/index.html`**

```html
<!doctype html>
<html lang="zh-TW">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>mediaflow</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 8: Create `frontend/src/main.tsx`**

```typescript
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

- [ ] **Step 9: Create `frontend/src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  body {
    @apply bg-neutral-950 text-neutral-100 font-mono;
  }
  * {
    @apply border-neutral-800;
  }
}
```

- [ ] **Step 10: Create `frontend/nginx.conf`**

```nginx
server {
    listen 3000;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://api:8080/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_set_header Connection '';
        chunked_transfer_encoding on;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 11: Create `frontend/Dockerfile`**

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s CMD wget -qO- http://localhost:3000/ || exit 1
```

- [ ] **Step 12: Update `docker-compose.yml` — replace `web` service build block**

Change lines 99-109:

```yaml
  web:
    build:
      context: ./frontend
    restart: unless-stopped
    ports:
      - "3000:3000"
    depends_on:
      - api
```

- [ ] **Step 13: Create placeholder `frontend/src/App.tsx` so the build compiles**

```typescript
export default function App() {
  return <div className="p-8 text-purple-400">mediaflow loading…</div>
}
```

- [ ] **Step 14: Run `npm install` and `npm run build`**

```bash
cd frontend
npm install
npm run build
```

Expected: `dist/` created, exit 0. TypeScript errors are OK at this stage (App.tsx is a placeholder).

- [ ] **Step 15: Commit**

```bash
git add frontend/ docker-compose.yml
git commit -m "feat(frontend): scaffold Vite + React + Tailwind + nginx Docker setup"
```

---

### Task 2: API additions — SSE stream + SRT save endpoint

**Files:**
- Create: `api/routes/stream.py`
- Modify: `api/routes/files.py` (add `PUT /{stem}/srt`)
- Modify: `api/main.py` (register `stream.router`)

**Interfaces:**
- Produces: `GET /events/stream` → SSE stream with `event: status` every 5s
- Produces: `PUT /files/{stem}/srt` → `{"saved": true, "bytes": N}`

- [ ] **Step 1: Write the test for `PUT /files/{stem}/srt`**

Create `tests/test_srt_save.py`:

```python
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

def test_save_srt_writes_file(tmp_path, monkeypatch):
    """PUT /files/{stem}/srt overwrites the SRT file and returns bytes written."""
    srt = tmp_path / "lesson01.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n", encoding="utf-8")

    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    # Re-import routes after env patch so WORKSPACE is re-evaluated
    import importlib, api.routes.files as m
    importlib.reload(m)

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(m.router)
    client = TestClient(app)

    new_content = "1\n00:00:01,000 --> 00:00:02,000\n已編輯\n\n"
    resp = client.put("/files/lesson01/srt", json={"content": new_content})
    assert resp.status_code == 200
    assert resp.json()["saved"] is True
    assert srt.read_text(encoding="utf-8") == new_content

def test_save_srt_404_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import importlib, api.routes.files as m
    importlib.reload(m)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(m.router)
    client = TestClient(app)
    resp = client.put("/files/nonexistent/srt", json={"content": "x"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test — expect FAIL (endpoint doesn't exist yet)**

```bash
cd /path/to/mediaflow
source venv/bin/activate
pytest tests/test_srt_save.py -v
```

Expected: `FAILED` — `405 Method Not Allowed` or `404`

- [ ] **Step 3: Add `PUT /{stem}/srt` to `api/routes/files.py`**

After the existing `get_srt` function (around line 42), add:

```python
@router.put("/{stem}/srt")
def save_srt(stem: str, body: dict = Body(...)):
    path = _srt_path(stem)
    if not path.exists():
        raise HTTPException(status_code=404, detail="SRT not found")
    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content must be a string")
    path.write_text(content, encoding="utf-8")
    return {"saved": True, "bytes": len(content.encode())}
```

Also add `Body` to the existing import line:
```python
from fastapi import APIRouter, Body, HTTPException, Query
```

- [ ] **Step 4: Run test — expect PASS**

```bash
pytest tests/test_srt_save.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Create `api/routes/stream.py`**

```python
"""Server-sent events — pushes pipeline status to the browser every 5 s."""
import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.db.queries import get_status_overview

router = APIRouter(prefix="/events")
log = logging.getLogger(__name__)


@router.get("/stream")
async def sse_stream(request: Request):
    pool = request.app.state.pool

    async def generator():
        yield "retry: 5000\n\n"
        while not await request.is_disconnected():
            try:
                data = await get_status_overview(pool)
                payload = json.dumps(data)
                yield f"event: status\ndata: {payload}\n\n"
            except Exception as exc:
                log.warning("SSE error: %s", exc)
            await asyncio.sleep(5)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
```

- [ ] **Step 6: Register `stream.router` in `api/main.py`**

Add import:
```python
from api.routes import clip, correction, dag_callback, events, files, jobs as jobs_router, stats, status, stream, tasks, upload
```

Add after `app.include_router(status.router)`:
```python
app.include_router(stream.router)
```

**Important:** `stream.router` has prefix `/events` — same prefix as `events.router`. The two routes don't conflict (`events.router` has `POST /stage-complete`; `stream.router` has `GET /stream`). FastAPI merges them correctly.

- [ ] **Step 7: Smoke-test the SSE endpoint**

Rebuild the API container and test:

```bash
bash scripts/ctl.sh rebuild api
curl -N http://localhost:8080/events/stream
```

Expected output (lines arrive every 5s):
```
retry: 5000

event: status
data: {"processing": [...], "queue": [...], "recent": [...], "failed": [...]}

event: status
data: {...}
```

Press Ctrl-C to stop. Exit code doesn't matter.

- [ ] **Step 8: Commit**

```bash
git add api/routes/stream.py api/routes/files.py api/main.py tests/test_srt_save.py
git commit -m "feat(api): add SSE /events/stream + PUT /files/{stem}/srt"
```

---

### Task 3: TypeScript API types + client

**Files:**
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/client.test.ts`

**Interfaces:**
- Produces: `api` object imported as `import { api } from '@/api/client'`
- All functions return typed promises matching `types.ts`

- [ ] **Step 1: Write the test first**

Create `frontend/src/api/client.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'

// stub global fetch
const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

// stub window.location so URL() works in jsdom
Object.defineProperty(window, 'location', {
  value: { origin: 'http://localhost:3000' },
  writable: true,
})

describe('api client', () => {
  beforeEach(() => mockFetch.mockReset())

  it('getStatus calls /api/status/', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ processing: [], queue: [], recent: [], failed: [] }),
    })
    const { api } = await import('./client')
    const result = await api.getStatus()
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/status/'),
    )
    expect(result.processing).toEqual([])
  })

  it('saveSrt sends PUT with content', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ saved: true, bytes: 5 }) })
    const { api } = await import('./client')
    await api.saveSrt('lesson01', 'hello')
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/files/lesson01/srt'),
      expect.objectContaining({ method: 'PUT' }),
    )
  })

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 404 })
    const { api } = await import('./client')
    await expect(api.getFiles()).rejects.toThrow('404')
  })
})
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd frontend
npm test
```

Expected: `Cannot find module './client'`

- [ ] **Step 3: Create `frontend/src/api/types.ts`**

```typescript
export interface Task {
  id: string
  stem: string
  filename: string
  status: string
  current_stage: string | null
  submitted_at: number | null
  started_at: number | null
  completed_at: number | null
  duration_sec: number | null
  error_msg: string | null
  retry_count?: number
}

export interface StatusOverview {
  processing: Task[]
  queue: Task[]
  recent: Task[]
  failed: Task[]
}

export interface SrtFile {
  stem: string
  size_kb: number
  mtime: number
}

export interface Segment {
  index: number
  start: string
  end: string
  start_seconds: number
  text: string
}

export interface SpeakerData {
  speakers: string[]
  counts: Record<string, number>
  names: Record<string, string>
  has_audio: boolean
}

export interface TimelineStage {
  stage: string
  completed_at: number | null
  duration_sec: number | null
}

export interface TaskTimeline {
  stem: string
  filename: string | null
  submitted_at: number | null
  started_at: number | null
  completed_at: number | null
  total_pipeline_sec: number
  total_wall_sec: number | null
  stages: TimelineStage[]
}

export interface StatsOverview {
  total_tasks: number
  total_duration_sec: number
  success_rate: number
  speakers: { label: string; seconds: number; pct: number }[]
}

export interface Keyword {
  topic: string
  count: number
}

export interface UploadInitRequest {
  filename: string
  size_bytes: number
  content_type: string
}

export interface UploadPart {
  part_number: number
  url: string
}

export interface UploadInitResponse {
  upload_id: string
  minio_key: string
  stem: string
  part_size: number
  parts: UploadPart[]
}
```

- [ ] **Step 4: Create `frontend/src/api/client.ts`**

```typescript
import type {
  StatusOverview, SrtFile, Segment, SpeakerData, TaskTimeline,
  StatsOverview, Keyword, UploadInitRequest, UploadInitResponse,
} from './types'

const BASE = '/api'

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin)
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))
  const res = await fetch(url.toString())
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`PUT ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

async function del(path: string): Promise<void> {
  await fetch(BASE + path, { method: 'DELETE' })
}

export const api = {
  getStatus:       () => get<StatusOverview>('/status/'),
  getFiles:        () => get<SrtFile[]>('/files/'),
  getSegments:     (stem: string, q?: string) =>
                     get<Segment[]>(`/files/${stem}/segments`, q ? { q } : undefined),
  getSpeakerData:  (stem: string) => get<SpeakerData>(`/files/${stem}/speaker-names`),
  setSpeakerNames: (stem: string, names: Record<string, string>) =>
                     post<{ saved: number }>(`/files/${stem}/speaker-names`, names),
  getTimeline:     (stem: string) => get<TaskTimeline>(`/tasks/${stem}/timeline`),
  getSummary:      (stem: string): Promise<string | null> =>
                     fetch(`${BASE}/files/${stem}/summary`).then(r => r.ok ? r.text() : null),
  saveSrt:         (stem: string, content: string) =>
                     put<{ saved: boolean; bytes: number }>(`/files/${stem}/srt`, { content }),
  getRawSrt:       (stem: string): Promise<string> =>
                     fetch(`${BASE}/files/${stem}/srt`).then(r => {
                       if (!r.ok) throw new Error(`GET /files/${stem}/srt → ${r.status}`)
                       return r.text()
                     }),
  getStatsOverview: () => get<StatsOverview>('/stats/overview'),
  getKeywords:     () => get<Keyword[]>('/stats/keywords'),
  uploadInit:      (req: UploadInitRequest) => post<UploadInitResponse>('/upload/init', req),
  uploadComplete:  (body: { upload_id: string; minio_key: string; parts: { part_number: number; etag: string }[] }) =>
                     post<{ stem: string; status: string }>('/upload/complete', body),
  createJob:       (file_key: string) =>
                     post<{ job_id: string; status: string }>('/jobs', { file_key, dag_flow: null }),
  rerunTask:       (stem: string, from_stage: string | null) =>
                     post<{ stem: string; status: string }>(`/tasks/${stem}/runs`, { from_stage }),
  cancelTask:      (stem: string) => del(`/tasks/${stem}`),
  audioUrl:        (stem: string) => `${BASE}/files/${stem}/audio`,
  sseUrl:          () => `${BASE}/events/stream`,
}
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
cd frontend
npm test
```

Expected: `3 passed`

- [ ] **Step 6: Commit**

```bash
cd ..
git add frontend/src/api/
git commit -m "feat(frontend): add TypeScript API types and client"
```

---

### Task 4: App shell — Router + Layout

**Files:**
- Create: `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/App.tsx` (full implementation)

**Interfaces:**
- Produces: `<Layout />` wraps pages via `<Outlet />`; nav links highlight active route

- [ ] **Step 1: Create `frontend/src/components/Layout.tsx`**

```typescript
import { useState, useEffect } from 'react'
import { Outlet, NavLink } from 'react-router-dom'

function LiveClock() {
  const [clock, setClock] = useState('')
  useEffect(() => {
    const tick = () => {
      const d = new Date()
      setClock(d.toISOString().replace('T', ' ').slice(0, 19))
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="text-neutral-500 text-xs tabular-nums">{clock}</span>
}

const navCls = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'text-purple-400' : 'text-neutral-400 hover:text-neutral-200 transition-colors'

export function Layout() {
  return (
    <div className="min-h-screen flex flex-col bg-neutral-950 text-neutral-100 font-mono">
      <header className="border-b border-neutral-800 px-6 py-3 flex items-center justify-between flex-shrink-0">
        <div className="text-lg font-bold tracking-tight">
          media<span className="text-purple-400">flow</span>
        </div>
        <div className="flex items-center gap-6 text-sm">
          <nav className="flex gap-5">
            <NavLink to="/" end className={navCls}>dashboard</NavLink>
            <NavLink to="/transcripts" className={navCls}>transcripts</NavLink>
            <NavLink to="/upload" className={navCls}>upload</NavLink>
          </nav>
          <span className="flex items-center gap-1.5 text-neutral-400 text-xs">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            live
          </span>
          <LiveClock />
        </div>
      </header>

      <main className="flex-1 max-w-5xl w-full mx-auto px-6 py-6">
        <Outlet />
      </main>

      <footer className="border-t border-neutral-800 px-6 py-3 text-xs text-neutral-600 flex justify-between flex-shrink-0">
        <span>mediaflow pipeline monitor</span>
        <span>SSE live</span>
      </footer>
    </div>
  )
}
```

- [ ] **Step 2: Replace `frontend/src/App.tsx` with full router setup**

```typescript
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { lazy, Suspense } from 'react'

const Dashboard   = lazy(() => import('@/pages/Dashboard').then(m => ({ default: m.Dashboard })))
const Transcripts = lazy(() => import('@/pages/Transcripts').then(m => ({ default: m.Transcripts })))
const SrtViewer   = lazy(() => import('@/pages/SrtViewer').then(m => ({ default: m.SrtViewer })))
const Upload      = lazy(() => import('@/pages/Upload').then(m => ({ default: m.Upload })))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Suspense fallback={<div className="p-8 text-neutral-500">loading…</div>}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/"                     element={<Dashboard />} />
              <Route path="/transcripts"          element={<Transcripts />} />
              <Route path="/transcripts/:stem"    element={<SrtViewer />} />
              <Route path="/upload"               element={<Upload />} />
              <Route path="*"                     element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
```

- [ ] **Step 3: Create stub page files so App.tsx compiles**

`frontend/src/pages/Dashboard.tsx`:
```typescript
export function Dashboard() { return <div className="text-neutral-400">Dashboard — coming soon</div> }
```

`frontend/src/pages/Transcripts.tsx`:
```typescript
export function Transcripts() { return <div className="text-neutral-400">Transcripts — coming soon</div> }
```

`frontend/src/pages/SrtViewer.tsx`:
```typescript
export function SrtViewer() { return <div className="text-neutral-400">SRT Viewer — coming soon</div> }
```

`frontend/src/pages/Upload.tsx`:
```typescript
export function Upload() { return <div className="text-neutral-400">Upload — coming soon</div> }
```

- [ ] **Step 4: Build and verify**

```bash
cd frontend
npm run build
```

Expected: exit 0, no TypeScript errors.

- [ ] **Step 5: Start dev server and verify navigation**

```bash
npm run dev
```

Open `http://localhost:5173`. Verify:
- Header shows `mediaflow` wordmark with purple accent
- Nav links `dashboard`, `transcripts`, `upload` present
- Clicking each link shows the stub text
- Active link turns purple
- Live clock ticks in header

- [ ] **Step 6: Commit**

```bash
cd ..
git add frontend/src/
git commit -m "feat(frontend): app shell — Router, Layout, live clock, stub pages"
```

---

### Task 5: Dashboard page

**Files:**
- Create: `frontend/src/hooks/useSSE.ts`
- Create: `frontend/src/components/StatusBar.tsx`
- Create: `frontend/src/components/JobList.tsx`
- Create: `frontend/src/components/TaskAccordion.tsx`
- Modify: `frontend/src/pages/Dashboard.tsx`

**Interfaces:**
- Consumes: `api.getStatus()` → `StatusOverview`; SSE at `/api/events/stream`
- Produces: live dashboard that updates within 5s of pipeline events

- [ ] **Step 1: Create `frontend/src/hooks/useSSE.ts`**

```typescript
import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { StatusOverview } from '@/api/types'
import { api } from '@/api/client'

export function useSSE() {
  const queryClient = useQueryClient()

  useEffect(() => {
    const es = new EventSource(api.sseUrl())

    es.addEventListener('status', (e: MessageEvent) => {
      const data = JSON.parse(e.data) as StatusOverview
      // Normalise: add stem alias matching what the DB shim adds
      const norm = (tasks: StatusOverview[keyof StatusOverview]) =>
        tasks.map(t => ({ ...t, stem: t.stem ?? t.id }))
      queryClient.setQueryData<StatusOverview>(['status'], {
        processing: norm(data.processing),
        queue: norm(data.queue),
        recent: norm(data.recent),
        failed: norm(data.failed),
      })
    })

    return () => es.close()
  }, [queryClient])
}
```

- [ ] **Step 2: Create `frontend/src/components/StatusBar.tsx`**

```typescript
import type { StatusOverview } from '@/api/types'

const CELLS = [
  { key: 'processing' as const, label: 'Processing', color: 'text-blue-400',   border: 'border-blue-800' },
  { key: 'queue'      as const, label: 'Queued',     color: 'text-yellow-400', border: 'border-yellow-800' },
  { key: 'recent'     as const, label: 'Completed',  color: 'text-green-400',  border: 'border-green-800' },
  { key: 'failed'     as const, label: 'Failed',     color: 'text-red-400',    border: 'border-red-800' },
]

export function StatusBar({ data }: { data: StatusOverview }) {
  return (
    <div className="grid grid-cols-4 gap-3 mb-6">
      {CELLS.map(({ key, label, color, border }) => (
        <div key={key} className={`bg-neutral-900 border ${border} rounded-lg p-4 text-center`}>
          <div className={`text-3xl font-bold tabular-nums ${color}`}>{data[key].length}</div>
          <div className="text-xs text-neutral-500 mt-1 uppercase tracking-widest">{label}</div>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: Create `frontend/src/components/TaskAccordion.tsx`**

```typescript
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { Task } from '@/api/types'

const STAGES = ['preprocess', 'transcribe', 'diarize', 'summarize'] as const
const RERUN_STAGES = [
  { value: '', label: '完整重跑' },
  { value: 'transcribe', label: 'transcribe' },
  { value: 'summarize', label: 'summarize' },
  { value: 'detect_chapters', label: 'detect_chapters' },
]

function fmtDuration(sec: number | null) {
  if (!sec) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

export function TaskAccordion({ task }: { task: Task }) {
  const [open, setOpen] = useState(false)
  const [rerunStage, setRerunStage] = useState('')
  const qc = useQueryClient()

  const { data: detail } = useQuery({
    queryKey: ['task-detail', task.stem],
    queryFn: async () => {
      const [timeline, summary, segments] = await Promise.all([
        api.getTimeline(task.stem),
        api.getSummary(task.stem),
        api.getSegments(task.stem),
      ])
      return { timeline, summary, segments: segments.slice(0, 3) }
    },
    enabled: open,
    staleTime: 60_000,
  })

  const rerun = useMutation({
    mutationFn: () => api.rerunTask(task.stem, rerunStage || null),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })

  const stem = task.stem ?? task.id

  return (
    <div className="border border-neutral-800 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-neutral-900 transition-colors text-left"
        onClick={() => setOpen(o => !o)}
      >
        <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
        <span className="flex-1 text-sm truncate">{task.filename || stem}</span>
        <span className="text-xs text-neutral-500">{fmtDuration(task.duration_sec)}</span>
        <span className="text-neutral-600 text-xs">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="border-t border-neutral-800 px-4 py-3 bg-neutral-950 grid md:grid-cols-2 gap-4">
          {/* Left: summary + timeline */}
          <div>
            <div className="text-xs text-neutral-500 uppercase tracking-wider mb-2">摘要</div>
            <p className="text-xs text-neutral-300 leading-relaxed line-clamp-3">
              {detail?.summary?.slice(0, 200) ?? '（載入中…）'}
            </p>

            {detail?.timeline?.stages && detail.timeline.stages.length > 0 && (
              <>
                <div className="text-xs text-neutral-500 uppercase tracking-wider mt-4 mb-2">各階段耗時</div>
                {(() => {
                  const maxDur = Math.max(...detail.timeline.stages.map(s => s.duration_sec ?? 0), 1)
                  return detail.timeline.stages.map(s => (
                    <div key={s.stage} className="flex items-center gap-2 mb-1">
                      <span className="text-xs text-neutral-500 w-24 truncate">{s.stage}</span>
                      <div className="flex-1 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-purple-500 rounded-full"
                          style={{ width: `${((s.duration_sec ?? 0) / maxDur) * 100}%` }}
                        />
                      </div>
                      <span className="text-xs text-neutral-500 w-12 text-right tabular-nums">
                        {fmtDuration(s.duration_sec)}
                      </span>
                    </div>
                  ))
                })()}
              </>
            )}
          </div>

          {/* Right: transcript preview + actions */}
          <div>
            <div className="text-xs text-neutral-500 uppercase tracking-wider mb-2">逐字稿（前幾段）</div>
            <div className="space-y-1">
              {detail?.segments?.map(seg => (
                <div key={seg.index} className="flex gap-2 text-xs">
                  <span className="text-neutral-600 tabular-nums w-16 flex-shrink-0">{seg.start.slice(0, 8)}</span>
                  <span className="text-neutral-300 truncate">{seg.text}</span>
                </div>
              )) ?? <span className="text-xs text-neutral-600">載入中…</span>}
            </div>

            <a
              href={`/transcripts/${stem}`}
              className="inline-block mt-3 text-xs text-purple-400 hover:underline"
            >
              → 開啟完整逐字稿
            </a>

            <div className="mt-4 pt-3 border-t border-neutral-800 flex items-center gap-2">
              <span className="text-xs text-neutral-600">rerun from:</span>
              <select
                value={rerunStage}
                onChange={e => setRerunStage(e.target.value)}
                className="text-xs bg-neutral-900 border border-neutral-700 rounded px-2 py-1 text-neutral-300"
              >
                {RERUN_STAGES.map(s => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
              <button
                onClick={() => rerun.mutate()}
                disabled={rerun.isPending}
                className="text-xs px-3 py-1 bg-yellow-900 text-yellow-200 border border-yellow-700 rounded hover:bg-yellow-800 disabled:opacity-50"
              >
                {rerun.isPending ? '…' : 'run'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Create `frontend/src/components/JobList.tsx`**

```typescript
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { TaskAccordion } from './TaskAccordion'
import type { StatusOverview, Task } from '@/api/types'

const PIPELINE_STAGES = ['preprocess', 'transcribe', 'diarize', 'summarize']

function StagePips({ current }: { current: string | null }) {
  const curIdx = current ? PIPELINE_STAGES.indexOf(current) : -1
  return (
    <div className="flex gap-1">
      {PIPELINE_STAGES.map((s, i) => (
        <div
          key={s}
          title={s}
          className={`w-2 h-2 rounded-full ${
            s === current ? 'bg-blue-400 animate-pulse' :
            i < curIdx    ? 'bg-neutral-500' :
                            'bg-neutral-800'
          }`}
        />
      ))}
    </div>
  )
}

function Section({ title, count, colorClass, children }: {
  title: string; count: number; colorClass: string; children: React.ReactNode
}) {
  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm font-semibold text-neutral-300">{title}</span>
        {count > 0 && (
          <span className={`text-xs px-1.5 py-0.5 rounded ${colorClass}`}>{count}</span>
        )}
      </div>
      {children}
    </div>
  )
}

function CancelButton({ stem }: { stem: string }) {
  const qc = useQueryClient()
  const cancel = useMutation({
    mutationFn: () => api.cancelTask(stem),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  return (
    <button
      onClick={() => { if (confirm(`Cancel ${stem}?`)) cancel.mutate() }}
      className="text-xs px-2 py-0.5 bg-red-950 text-red-300 border border-red-800 rounded hover:bg-red-900"
    >
      cancel
    </button>
  )
}

export function JobList({ data }: { data: StatusOverview }) {
  return (
    <div>
      {/* Now Processing */}
      <Section title="Now Processing" count={data.processing.length} colorClass="bg-blue-950 text-blue-300">
        {data.processing.length === 0 ? (
          <p className="text-xs text-neutral-600">No active jobs</p>
        ) : (
          <div className="space-y-2">
            {data.processing.map(t => (
              <div key={t.stem} className="flex items-center gap-3 bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-3">
                <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse flex-shrink-0" />
                <span className="flex-1 text-sm truncate">{t.filename || t.stem}</span>
                <StagePips current={t.current_stage} />
                <span className="text-xs text-neutral-500">{t.current_stage ?? '—'}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Queue */}
      {data.queue.length > 0 && (
        <Section title="Queue" count={data.queue.length} colorClass="bg-yellow-950 text-yellow-300">
          <div className="space-y-2">
            {data.queue.map(t => (
              <div key={t.stem} className="flex items-center gap-3 bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-3">
                <span className="w-2 h-2 rounded-full bg-yellow-400 flex-shrink-0" />
                <span className="flex-1 text-sm truncate">{t.filename || t.stem}</span>
                <span className="text-xs text-neutral-500">waiting</span>
                <CancelButton stem={t.stem} />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Recent Completions */}
      <Section title="Recent Completions" count={data.recent.length} colorClass="bg-green-950 text-green-300">
        {data.recent.length === 0 ? (
          <p className="text-xs text-neutral-600">No completions yet</p>
        ) : (
          <div className="space-y-2">
            {data.recent.map(t => <TaskAccordion key={t.stem} task={t} />)}
          </div>
        )}
      </Section>

      {/* Failed */}
      {data.failed.length > 0 && (
        <Section title="Failed" count={data.failed.length} colorClass="bg-red-950 text-red-300">
          <div className="space-y-2">
            {data.failed.map(t => (
              <div key={t.stem} className="flex items-center gap-3 bg-neutral-900 border border-red-900 rounded-lg px-4 py-3">
                <span className="w-2 h-2 rounded-full bg-red-400 flex-shrink-0" />
                <span className="flex-1 text-sm truncate">{t.filename || t.stem}</span>
                <span className="text-xs text-red-400 truncate max-w-xs" title={t.error_msg ?? ''}>
                  {t.error_msg || 'unknown error'}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Replace `frontend/src/pages/Dashboard.tsx`**

```typescript
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { StatusBar } from '@/components/StatusBar'
import { JobList } from '@/components/JobList'
import { useSSE } from '@/hooks/useSSE'
import type { StatusOverview } from '@/api/types'

const EMPTY: StatusOverview = { processing: [], queue: [], recent: [], failed: [] }

export function Dashboard() {
  useSSE()

  const { data = EMPTY, isError } = useQuery({
    queryKey: ['status'],
    queryFn: api.getStatus,
    refetchInterval: 30_000,
  })

  if (isError) {
    return <p className="text-red-400 text-sm">API unreachable — check that the api container is running.</p>
  }

  return (
    <div>
      <StatusBar data={data} />
      <JobList data={data} />
    </div>
  )
}
```

- [ ] **Step 6: Build and smoke test**

```bash
cd frontend
npm run build
```

Expected: exit 0.

Start dev server (`npm run dev`), open `http://localhost:5173`. Verify:
- Status bar shows 4 cells with live counts
- "Now Processing", "Queue", "Recent Completions", "Failed" sections appear
- Clicking a completed task expands the accordion
- Rerun dropdown and button are visible in accordion

- [ ] **Step 7: Commit**

```bash
cd ..
git add frontend/src/
git commit -m "feat(frontend): dashboard — StatusBar, JobList, TaskAccordion, SSE hook"
```

---

### Task 6: Stats panel + Transcripts list page

**Files:**
- Create: `frontend/src/components/StatsPanel.tsx`
- Modify: `frontend/src/pages/Dashboard.tsx` (add StatsPanel)
- Modify: `frontend/src/pages/Transcripts.tsx` (full implementation)

**Interfaces:**
- Consumes: `api.getStatsOverview()`, `api.getKeywords()`, `api.getFiles()`
- Produces: speaker bar chart, keyword table, SRT file list with links

- [ ] **Step 1: Create `frontend/src/components/StatsPanel.tsx`**

```typescript
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

function fmtSec(sec: number) {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export function StatsPanel() {
  const { data: overview } = useQuery({
    queryKey: ['stats-overview'],
    queryFn: api.getStatsOverview,
    staleTime: 60_000,
  })
  const { data: keywords = [] } = useQuery({
    queryKey: ['keywords'],
    queryFn: api.getKeywords,
    staleTime: 60_000,
  })

  if (!overview) return null

  return (
    <div className="grid md:grid-cols-2 gap-4 mb-6">
      {/* Speaker bar chart */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4">
        <div className="text-xs text-neutral-500 uppercase tracking-wider mb-3">說話者分佈</div>
        {overview.speakers.length === 0 ? (
          <p className="text-xs text-neutral-600">無說話者資料（需開啟 diarize 階段）</p>
        ) : (
          <div className="space-y-2">
            {overview.speakers.map(sp => (
              <div key={sp.label} className="flex items-center gap-2">
                <span className="text-xs text-neutral-400 w-24 truncate">{sp.label}</span>
                <div className="flex-1 h-2 bg-neutral-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-purple-500 rounded-full"
                    style={{ width: `${sp.pct * 100}%` }}
                  />
                </div>
                <span className="text-xs text-neutral-500 tabular-nums w-12 text-right">
                  {fmtSec(sp.seconds)}
                </span>
              </div>
            ))}
          </div>
        )}
        <div className="mt-3 pt-3 border-t border-neutral-800 flex gap-4 text-xs text-neutral-500">
          <span>{overview.total_tasks} 個任務</span>
          <span>{fmtSec(overview.total_duration_sec)} 總時長</span>
          <span>{Math.round(overview.success_rate * 100)}% 成功率</span>
        </div>
      </div>

      {/* Keyword frequency table */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4">
        <div className="text-xs text-neutral-500 uppercase tracking-wider mb-3">高頻主題</div>
        {keywords.length === 0 ? (
          <p className="text-xs text-neutral-600">無主題資料</p>
        ) : (
          <div className="space-y-1">
            {keywords.map(kw => (
              <div key={kw.topic} className="flex items-center justify-between">
                <span className="text-xs text-neutral-300 truncate flex-1">{kw.topic}</span>
                <span className="text-xs text-neutral-600 tabular-nums ml-2">{kw.count}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add `<StatsPanel />` to `frontend/src/pages/Dashboard.tsx`**

Add import at top:
```typescript
import { StatsPanel } from '@/components/StatsPanel'
```

Insert `<StatsPanel />` before `<StatusBar data={data} />`:
```typescript
return (
  <div>
    <StatsPanel />
    <StatusBar data={data} />
    <JobList data={data} />
  </div>
)
```

- [ ] **Step 3: Replace `frontend/src/pages/Transcripts.tsx`**

```typescript
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'

function fmtDate(mtime: number) {
  return new Date(mtime * 1000).toLocaleString('zh-TW', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

export function Transcripts() {
  const { data: files = [], isLoading } = useQuery({
    queryKey: ['files'],
    queryFn: api.getFiles,
    staleTime: 30_000,
  })

  return (
    <div>
      <div className="flex items-center gap-3 mb-5">
        <h1 className="text-base font-semibold text-neutral-200">Transcripts</h1>
        <span className="text-xs px-1.5 py-0.5 bg-neutral-800 text-neutral-400 rounded">
          {files.length}
        </span>
      </div>

      {isLoading ? (
        <p className="text-xs text-neutral-600">載入中…</p>
      ) : files.length === 0 ? (
        <p className="text-xs text-neutral-600">workspace/3_output/ 中沒有逐字稿檔案</p>
      ) : (
        <div className="space-y-1">
          {files.map(f => (
            <Link
              key={f.stem}
              to={`/transcripts/${f.stem}`}
              className="flex items-center gap-3 px-4 py-3 bg-neutral-900 border border-neutral-800 rounded-lg hover:border-purple-700 hover:bg-neutral-800 transition-colors"
            >
              <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
              <span className="flex-1 text-sm text-neutral-200">{f.stem}</span>
              <span className="text-xs text-neutral-600 tabular-nums">{f.size_kb} KB</span>
              <span className="text-xs text-neutral-600">{fmtDate(f.mtime)}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Build and verify**

```bash
cd frontend && npm run build
```

Open dev server, navigate to `/transcripts`. Verify list of SRT files appears with timestamps. Verify Dashboard shows stats panel.

- [ ] **Step 5: Commit**

```bash
cd ..
git add frontend/src/
git commit -m "feat(frontend): StatsPanel + Transcripts list page"
```

---

### Task 7: SRT Viewer page (audio player + segments + timeline)

**Files:**
- Create: `frontend/src/components/AudioPlayer.tsx`
- Create: `frontend/src/components/SrtSegmentList.tsx`
- Create: `frontend/src/components/TimelinePanel.tsx`
- Modify: `frontend/src/pages/SrtViewer.tsx`

**Interfaces:**
- Consumes: `api.getSegments(stem, q)`, `api.getSpeakerData(stem)`, `api.getTimeline(stem)`, `api.audioUrl(stem)`
- Produces: transcript viewer with audio player, clickable segments, search, and timeline

- [ ] **Step 1: Create `frontend/src/components/AudioPlayer.tsx`**

```typescript
import { useRef, useState, useCallback } from 'react'

function fmt(s: number) {
  if (!isFinite(s)) return '0:00'
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

interface Props {
  src: string
  onTimeUpdate?: (t: number) => void
  seekTo?: (fn: (t: number) => void) => void
}

export function AudioPlayer({ src, onTimeUpdate }: Props) {
  const ref = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [pct, setPct] = useState(0)
  const [label, setLabel] = useState('0:00 / 0:00')

  const toggle = () => {
    if (!ref.current) return
    ref.current.paused ? ref.current.play() : ref.current.pause()
  }

  const handleTime = () => {
    const el = ref.current
    if (!el) return
    const p = el.duration ? (el.currentTime / el.duration) * 100 : 0
    setPct(p)
    setLabel(`${fmt(el.currentTime)} / ${fmt(el.duration)}`)
    onTimeUpdate?.(el.currentTime)
  }

  const seek = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const el = ref.current
    if (!el?.duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    el.currentTime = ((e.clientX - rect.left) / rect.width) * el.duration
  }, [])

  return (
    <div className="sticky top-0 z-10 flex items-center gap-3 bg-neutral-900 border-b border-neutral-800 px-4 py-2">
      <audio
        ref={ref}
        src={src}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={handleTime}
      />
      <button
        onClick={toggle}
        className="w-8 h-8 flex items-center justify-center text-neutral-200 hover:text-purple-400"
      >
        {playing ? '⏸' : '▶'}
      </button>
      <div
        className="flex-1 h-1.5 bg-neutral-700 rounded-full cursor-pointer relative"
        onClick={seek}
      >
        <div className="absolute left-0 top-0 h-full bg-purple-500 rounded-full" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-neutral-500 tabular-nums w-28 text-right">{label}</span>
    </div>
  )
}

export function useAudioSeek() {
  const ref = useRef<HTMLAudioElement | null>(null)
  const seekTo = (t: number) => {
    if (ref.current) ref.current.currentTime = t
  }
  return { audioRef: ref, seekTo }
}
```

- [ ] **Step 2: Create `frontend/src/components/SrtSegmentList.tsx`**

```typescript
interface Segment {
  index: number
  start: string
  end: string
  start_seconds: number
  text: string
}

interface Props {
  segments: Segment[]
  currentTime?: number
  onSeek?: (t: number) => void
}

export function SrtSegmentList({ segments, currentTime = -1, onSeek }: Props) {
  if (segments.length === 0) {
    return <p className="text-xs text-neutral-600 py-4">無段落資料</p>
  }

  return (
    <div className="space-y-0">
      {segments.map((seg, i) => {
        const nextStart = segments[i + 1]?.start_seconds ?? Infinity
        const isActive = currentTime >= seg.start_seconds && currentTime < nextStart
        return (
          <div
            key={seg.index}
            data-start={seg.start_seconds}
            onClick={() => onSeek?.(seg.start_seconds)}
            className={`flex gap-3 px-4 py-2.5 cursor-pointer transition-colors border-l-2 ${
              isActive
                ? 'bg-purple-950/40 border-purple-500'
                : 'border-transparent hover:bg-neutral-900'
            }`}
          >
            <span className="text-xs text-neutral-600 tabular-nums w-16 flex-shrink-0 pt-0.5">
              {seg.start.slice(0, 8)}
            </span>
            <span
              className="text-sm text-neutral-200 leading-relaxed flex-1"
              dangerouslySetInnerHTML={{ __html: seg.text }}
            />
          </div>
        )
      })}
    </div>
  )
}
```

Note: `dangerouslySetInnerHTML` is safe here because `seg.text` comes from the API's `srtlib.highlight()` which only injects `<mark>` tags around matched text — no user-controlled HTML.

- [ ] **Step 3: Create `frontend/src/components/TimelinePanel.tsx`**

```typescript
import { useState } from 'react'
import type { TaskTimeline } from '@/api/types'

function fmtDur(sec: number | null) {
  if (!sec) return '—'
  return sec >= 60 ? `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s` : `${sec}s`
}

export function TimelinePanel({ timeline }: { timeline: TaskTimeline | null }) {
  const [open, setOpen] = useState(false)
  if (!timeline?.stages?.length) return null

  const maxDur = Math.max(...timeline.stages.map(s => s.duration_sec ?? 0), 1)

  return (
    <div className="mb-4 border border-neutral-800 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-neutral-900 transition-colors text-sm"
        onClick={() => setOpen(o => !o)}
      >
        <span className="text-neutral-400">各階段耗時</span>
        <span className="text-neutral-600 text-xs">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="border-t border-neutral-800 px-4 py-3 space-y-2 bg-neutral-950">
          {timeline.stages.map(s => (
            <div key={s.stage} className="flex items-center gap-3">
              <span className="text-xs text-neutral-500 w-28 truncate">{s.stage}</span>
              <div className="flex-1 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-purple-500 rounded-full"
                  style={{ width: `${((s.duration_sec ?? 0) / maxDur) * 100}%` }}
                />
              </div>
              <span className="text-xs text-neutral-500 tabular-nums w-16 text-right">
                {fmtDur(s.duration_sec)}
              </span>
            </div>
          ))}
          {timeline.total_wall_sec && (
            <div className="pt-2 border-t border-neutral-800 text-xs text-neutral-600">
              總計 {fmtDur(timeline.total_wall_sec)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Replace `frontend/src/pages/SrtViewer.tsx`**

```typescript
import { useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { AudioPlayer } from '@/components/AudioPlayer'
import { SrtSegmentList } from '@/components/SrtSegmentList'
import { TimelinePanel } from '@/components/TimelinePanel'
import { SpeakerPanel } from '@/components/SpeakerPanel'
import { SrtEditor } from '@/components/SrtEditor'

export function SrtViewer() {
  const { stem = '' } = useParams()
  const [q, setQ] = useState('')
  const [currentTime, setCurrentTime] = useState(-1)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const { data: speakerData } = useQuery({
    queryKey: ['speaker-data', stem],
    queryFn: () => api.getSpeakerData(stem),
    staleTime: 60_000,
  })
  const { data: segments = [] } = useQuery({
    queryKey: ['segments', stem, q],
    queryFn: () => api.getSegments(stem, q || undefined),
    staleTime: 30_000,
  })
  const { data: timeline = null } = useQuery({
    queryKey: ['timeline', stem],
    queryFn: () => api.getTimeline(stem),
    staleTime: 60_000,
  })

  const hasAudio = speakerData?.has_audio ?? false

  const seekTo = (t: number) => {
    if (audioRef.current) audioRef.current.currentTime = t
  }

  return (
    <div>
      {/* Audio player — sticky */}
      {hasAudio && (
        <AudioPlayer
          src={api.audioUrl(stem)}
          onTimeUpdate={setCurrentTime}
        />
      )}

      {/* Header */}
      <div className="flex items-center gap-4 mb-4 mt-4">
        <h1 className="text-sm font-semibold text-neutral-300 flex-1 truncate">{stem}</h1>
        <input
          type="search"
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="搜尋逐字稿…"
          className="w-56 text-sm bg-neutral-900 border border-neutral-700 rounded px-3 py-1.5 text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-purple-600"
        />
        <span className="text-xs text-neutral-600">{segments.length} 段</span>
      </div>

      {/* Speaker panel (only when diarization was used) */}
      {speakerData && speakerData.speakers.length > 0 && (
        <SpeakerPanel stem={stem} speakerData={speakerData} />
      )}

      {/* Timeline */}
      <TimelinePanel timeline={timeline} />

      {/* SRT Editor toggle */}
      <SrtEditor stem={stem} />

      {/* Transcript */}
      <SrtSegmentList
        segments={segments}
        currentTime={currentTime}
        onSeek={hasAudio ? seekTo : undefined}
      />

      {/* Footer nav */}
      <div className="mt-6 pt-4 border-t border-neutral-800 flex justify-between text-xs text-neutral-500">
        <Link to="/transcripts" className="hover:text-purple-400">← 返回逐字稿列表</Link>
        <span>{segments.length} 段落</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Create stub `SpeakerPanel` and `SrtEditor` so SrtViewer compiles**

`frontend/src/components/SpeakerPanel.tsx`:
```typescript
import type { SpeakerData } from '@/api/types'
export function SpeakerPanel(_: { stem: string; speakerData: SpeakerData }) {
  return null
}
```

`frontend/src/components/SrtEditor.tsx`:
```typescript
export function SrtEditor(_: { stem: string }) { return null }
```

- [ ] **Step 6: Build and verify**

```bash
cd frontend && npm run build
```

Navigate to a transcript URL like `http://localhost:5173/transcripts/test-speech`. Verify:
- Segments list appears
- Search box filters segments
- Timeline section appears (collapsed)
- Audio player appears at top if WAV exists

- [ ] **Step 7: Commit**

```bash
cd ..
git add frontend/src/
git commit -m "feat(frontend): SRT viewer — AudioPlayer, segments, timeline"
```

---

### Task 8: SRT Viewer extras — SpeakerPanel + SrtEditor

**Files:**
- Modify: `frontend/src/components/SpeakerPanel.tsx` (full implementation)
- Modify: `frontend/src/components/SrtEditor.tsx` (full implementation)

**Interfaces:**
- Consumes: `api.setSpeakerNames(stem, names)`, `api.getRawSrt(stem)`, `api.saveSrt(stem, content)`
- Produces: speaker label editor that updates transcript display; inline SRT editor with save

- [ ] **Step 1: Replace `frontend/src/components/SpeakerPanel.tsx`**

```typescript
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { SpeakerData } from '@/api/types'

export function SpeakerPanel({ stem, speakerData }: { stem: string; speakerData: SpeakerData }) {
  const [open, setOpen] = useState(false)
  const [names, setNames] = useState<Record<string, string>>(speakerData.names)
  const qc = useQueryClient()

  const save = useMutation({
    mutationFn: () => api.setSpeakerNames(stem, names),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['segments', stem] })
      qc.invalidateQueries({ queryKey: ['speaker-data', stem] })
    },
  })

  return (
    <div className="mb-4 border border-neutral-800 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-neutral-900 transition-colors text-sm"
        onClick={() => setOpen(o => !o)}
      >
        <span className="text-neutral-400">說話者標籤</span>
        <span className="text-neutral-600 text-xs">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="border-t border-neutral-800 px-4 py-3 bg-neutral-950">
          <div className="space-y-2">
            {speakerData.speakers.map(sp => (
              <div key={sp} className="flex items-center gap-3">
                <span className="text-xs text-neutral-500 w-28">{sp}</span>
                <span className="text-xs text-neutral-600 w-8 tabular-nums">
                  {speakerData.counts[sp] ?? 0}
                </span>
                <input
                  type="text"
                  value={names[sp] ?? ''}
                  onChange={e => setNames(n => ({ ...n, [sp]: e.target.value }))}
                  placeholder="輸入顯示名稱…"
                  className="flex-1 text-xs bg-neutral-900 border border-neutral-700 rounded px-2 py-1 text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-purple-600"
                />
              </div>
            ))}
          </div>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="mt-3 text-xs px-3 py-1.5 bg-purple-900 text-purple-200 border border-purple-700 rounded hover:bg-purple-800 disabled:opacity-50"
          >
            {save.isPending ? '儲存中…' : save.isSuccess ? '✓ 已儲存' : '儲存'}
          </button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Replace `frontend/src/components/SrtEditor.tsx`**

```typescript
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'

function parseSrt(text: string): { index: number; tc: string; text: string }[] {
  const blocks = text.trim().split(/\n\n+/)
  return blocks.flatMap(block => {
    const lines = block.trim().split('\n')
    if (lines.length < 3) return []
    const idx = parseInt(lines[0], 10)
    if (isNaN(idx)) return []
    return [{ index: idx, tc: lines[1], text: lines.slice(2).join('\n') }]
  })
}

function srtToText(segments: { index: number; tc: string; text: string }[]): string {
  return segments.map(s => `${s.index}\n${s.tc}\n${s.text}`).join('\n\n') + '\n'
}

export function SrtEditor({ stem }: { stem: string }) {
  const [editMode, setEditMode] = useState(false)
  const [edits, setEdits] = useState<Record<number, string>>({})
  const qc = useQueryClient()

  const { data: rawSrt = '' } = useQuery({
    queryKey: ['raw-srt', stem],
    queryFn: () => api.getRawSrt(stem),
    enabled: editMode,
    staleTime: Infinity,
  })

  const parsed = parseSrt(rawSrt)

  const save = useMutation({
    mutationFn: () => {
      const updated = parsed.map(s => ({
        ...s,
        text: edits[s.index] ?? s.text,
      }))
      return api.saveSrt(stem, srtToText(updated))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['segments', stem] })
      qc.invalidateQueries({ queryKey: ['raw-srt', stem] })
      setEdits({})
      setEditMode(false)
    },
  })

  if (!editMode) {
    return (
      <div className="mb-4 flex justify-end">
        <button
          onClick={() => setEditMode(true)}
          className="text-xs px-3 py-1 border border-neutral-700 text-neutral-400 rounded hover:border-purple-600 hover:text-purple-400 transition-colors"
        >
          編輯逐字稿
        </button>
      </div>
    )
  }

  return (
    <div className="mb-4 border border-purple-800 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 bg-purple-950/30 border-b border-purple-800">
        <span className="text-xs text-purple-300">編輯模式 — 直接修改文字內容</span>
        <div className="flex gap-2">
          <button
            onClick={() => { setEditMode(false); setEdits({}) }}
            className="text-xs px-3 py-1 border border-neutral-700 text-neutral-400 rounded hover:bg-neutral-800"
          >
            取消
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending || Object.keys(edits).length === 0}
            className="text-xs px-3 py-1 bg-purple-700 text-white rounded hover:bg-purple-600 disabled:opacity-50"
          >
            {save.isPending ? '儲存中…' : '儲存'}
          </button>
        </div>
      </div>

      <div className="max-h-96 overflow-y-auto">
        {parsed.map(seg => (
          <div key={seg.index} className="flex gap-3 px-4 py-2 border-b border-neutral-800 last:border-0">
            <span className="text-xs text-neutral-600 tabular-nums w-16 flex-shrink-0 pt-1.5">
              {seg.tc.slice(0, 8)}
            </span>
            <textarea
              value={edits[seg.index] ?? seg.text}
              onChange={e => setEdits(ed => ({ ...ed, [seg.index]: e.target.value }))}
              rows={Math.max(1, (edits[seg.index] ?? seg.text).split('\n').length)}
              className="flex-1 text-sm bg-transparent text-neutral-200 resize-none focus:outline-none leading-relaxed"
            />
          </div>
        ))}
      </div>

      {save.isError && (
        <div className="px-4 py-2 text-xs text-red-400 border-t border-red-900">
          儲存失敗 — {String(save.error)}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Build and smoke test**

```bash
cd frontend && npm run build
```

Navigate to a transcript with diarization. Verify:
- "說話者標籤" section is collapsible
- Input fields show existing names
- Save button invalidates and refreshes transcript

Navigate to any transcript. Verify:
- "編輯逐字稿" button appears
- Clicking it shows all segments in edit mode with textareas
- Editing and saving calls PUT /files/{stem}/srt and returns to read mode

- [ ] **Step 4: Commit**

```bash
cd ..
git add frontend/src/components/SpeakerPanel.tsx frontend/src/components/SrtEditor.tsx
git commit -m "feat(frontend): SRT viewer — SpeakerPanel + inline SrtEditor"
```

---

### Task 9: Upload page — drag-and-drop + multipart upload

**Files:**
- Create: `frontend/src/components/DropZone.tsx`
- Create: `frontend/src/components/UploadProgress.tsx`
- Modify: `frontend/src/pages/Upload.tsx`

**Interfaces:**
- Consumes: `api.uploadInit()`, `api.uploadComplete()`, `api.createJob()`
- Produces: drag-drop zone, per-file progress bars, upload state machine

- [ ] **Step 1: Create `frontend/src/components/DropZone.tsx`**

```typescript
import { useRef, useState } from 'react'

const ACCEPTED = new Set(['.mp4', '.m4a', '.mp3', '.wav', '.flac'])

function ext(name: string) {
  return name.slice(name.lastIndexOf('.')).toLowerCase()
}

interface Props {
  onFiles: (files: File[]) => void
}

export function DropZone({ onFiles }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handle = (files: FileList | null) => {
    if (!files) return
    const valid = Array.from(files).filter(f => ACCEPTED.has(ext(f.name)))
    if (valid.length) onFiles(valid)
  }

  return (
    <div
      className={`border-2 border-dashed rounded-xl p-12 text-center transition-colors cursor-pointer ${
        dragging ? 'border-purple-500 bg-purple-950/20' : 'border-neutral-700 hover:border-neutral-500'
      }`}
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); handle(e.dataTransfer.files) }}
    >
      <div className="text-4xl mb-3 text-neutral-500">⬆</div>
      <p className="text-neutral-400 mb-2">拖曳檔案到這裡，或</p>
      <button className="px-4 py-1.5 bg-purple-700 text-white text-sm rounded hover:bg-purple-600">
        選擇檔案
      </button>
      <p className="text-xs text-neutral-600 mt-3">
        支援 .mp4 .m4a .mp3 .wav .flac｜單檔上限 5 GB
      </p>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".mp4,.m4a,.mp3,.wav,.flac"
        className="hidden"
        onChange={e => handle(e.target.files)}
      />
    </div>
  )
}
```

- [ ] **Step 2: Create `frontend/src/components/UploadProgress.tsx`**

```typescript
type Status = 'waiting' | 'uploading' | 'done' | 'error'

interface FileState {
  file: File
  status: Status
  progress: number
  message: string
}

function fmtBytes(b: number) {
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`
  if (b >= 1e6) return `${(b / 1e6).toFixed(1)} MB`
  return `${(b / 1e3).toFixed(0)} KB`
}

const STATUS_COLOR: Record<Status, string> = {
  waiting:   'text-neutral-500',
  uploading: 'text-blue-400',
  done:      'text-green-400',
  error:     'text-red-400',
}

export function UploadProgress({ files }: { files: FileState[] }) {
  if (files.length === 0) return null
  return (
    <div className="mt-4 space-y-3">
      {files.map((f, i) => (
        <div key={i} className="bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-3">
          <div className="flex items-center gap-3 mb-2">
            <span className="flex-1 text-sm text-neutral-200 truncate">{f.file.name}</span>
            <span className="text-xs text-neutral-600 tabular-nums">{fmtBytes(f.file.size)}</span>
            <span className={`text-xs ${STATUS_COLOR[f.status]}`}>{f.message}</span>
          </div>
          <div className="h-1 bg-neutral-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300 ${
                f.status === 'done' ? 'bg-green-500' : f.status === 'error' ? 'bg-red-500' : 'bg-blue-500'
              }`}
              style={{ width: `${f.progress}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

export type { FileState }
```

- [ ] **Step 3: Replace `frontend/src/pages/Upload.tsx`**

```typescript
import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { DropZone } from '@/components/DropZone'
import { UploadProgress } from '@/components/UploadProgress'
import type { FileState } from '@/components/UploadProgress'

async function uploadFile(
  file: File,
  onProgress: (pct: number, msg: string) => void,
): Promise<void> {
  onProgress(0, '初始化中…')
  const init = await api.uploadInit({
    filename: file.name,
    size_bytes: file.size,
    content_type: file.type || 'application/octet-stream',
  })

  const completedParts: { part_number: number; etag: string }[] = []

  for (const part of init.parts) {
    const start = (part.part_number - 1) * init.part_size
    const chunk = file.slice(start, start + init.part_size)
    const pct = Math.round(((part.part_number - 1) / init.parts.length) * 90)
    onProgress(pct, `上傳中 ${part.part_number}/${init.parts.length}`)

    const res = await fetch(part.url, { method: 'PUT', body: chunk })
    if (!res.ok) throw new Error(`Part ${part.part_number} failed: ${res.status}`)
    const etag = res.headers.get('ETag') ?? res.headers.get('etag') ?? ''
    completedParts.push({ part_number: part.part_number, etag })
  }

  onProgress(90, '完成中…')
  await api.uploadComplete({ upload_id: init.upload_id, minio_key: init.minio_key, parts: completedParts })
  await api.createJob(init.minio_key)
  onProgress(100, '✓ 已加入佇列')
}

export function Upload() {
  const [queue, setQueue] = useState<FileState[]>([])
  const [running, setRunning] = useState(false)
  const navigate = useNavigate()

  const addFiles = useCallback((files: File[]) => {
    const existing = new Set(queue.map(f => f.file.name))
    const newEntries: FileState[] = files
      .filter(f => !existing.has(f.name))
      .map(f => ({ file: f, status: 'waiting', progress: 0, message: '等待中' }))
    setQueue(q => [...q, ...newEntries])
  }, [queue])

  const setFileState = (index: number, patch: Partial<FileState>) => {
    setQueue(q => q.map((f, i) => i === index ? { ...f, ...patch } : f))
  }

  const startUpload = async () => {
    setRunning(true)
    for (let i = 0; i < queue.length; i++) {
      if (queue[i].status === 'done') continue
      try {
        await uploadFile(queue[i].file, (progress, message) => {
          setFileState(i, {
            progress,
            message,
            status: progress === 100 ? 'done' : 'uploading',
          })
        })
      } catch (err) {
        setFileState(i, { status: 'error', message: `錯誤: ${String(err)}`, progress: 0 })
      }
    }
    setRunning(false)
  }

  const allDone = queue.length > 0 && queue.every(f => f.status === 'done')

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-base font-semibold text-neutral-200 mb-5">上傳音訊 / 視訊</h1>

      <DropZone onFiles={addFiles} />
      <UploadProgress files={queue} />

      {queue.length > 0 && !allDone && (
        <button
          onClick={startUpload}
          disabled={running}
          className="mt-4 w-full py-2.5 bg-purple-700 text-white rounded-lg hover:bg-purple-600 disabled:opacity-50 text-sm font-medium"
        >
          {running ? '上傳中…' : '開始上傳'}
        </button>
      )}

      {allDone && (
        <button
          onClick={() => navigate('/')}
          className="mt-4 w-full py-2.5 bg-green-800 text-green-100 rounded-lg hover:bg-green-700 text-sm font-medium"
        >
          完成 — 前往 Dashboard 查看進度
        </button>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Build**

```bash
cd frontend && npm run build
```

Expected: exit 0.

- [ ] **Step 5: Smoke test upload flow**

Start dev server (`npm run dev`) with the full stack running (`bash scripts/ctl.sh start all`).

Navigate to `http://localhost:5173/upload`:
1. Drag a `.m4a` file onto the drop zone — file appears in the list
2. Click "開始上傳" — progress bar advances, final message shows "✓ 已加入佇列"
3. Click "完成" — redirected to dashboard showing the new job in "Queue" or "Now Processing"

- [ ] **Step 6: Commit**

```bash
cd ..
git add frontend/src/components/DropZone.tsx frontend/src/components/UploadProgress.tsx frontend/src/pages/Upload.tsx
git commit -m "feat(frontend): upload page — DropZone + multipart upload state machine"
```

---

## Final validation

After all tasks complete, run:

```bash
# Build the React app
cd frontend && npm run build && cd ..

# Rebuild Docker web service
bash scripts/ctl.sh rebuild web

# Verify web is running
bash scripts/ctl.sh status
```

Verify at `http://localhost:3000`:
- [ ] Dashboard loads with SSE live updates (not polling page refresh)
- [ ] Stats panel shows speaker bar and keywords
- [ ] Transcripts list shows all SRT files
- [ ] SRT viewer opens, audio player appears when WAV exists, click-to-seek works
- [ ] Speaker label editor saves names and refreshes transcript
- [ ] Edit mode shows textareas; save writes to disk and returns to read mode
- [ ] Upload page accepts drag-and-drop and uploads to MinIO

Run the existing smoke test to confirm API still works:

```bash
bash tests/run-pipeline-test.sh
```

Expected: `5 passed, 0 failed`
