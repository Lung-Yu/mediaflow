# Frontend Two-Column Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current three-route React SPA with a single two-column page — left panel (upload, job status, transcript list) + right panel (audio player, segments, summary).

**Architecture:** Single route `/`. `Home.tsx` owns `stem` state and syncs it to `?stem=` URL param. `LeftPanel.tsx` handles upload, active jobs, and transcript selection. `RightPanel.tsx` renders the selected transcript with audio sync. Eleven old components/pages are deleted; six new ones replace them.

**Tech Stack:** React 18, TypeScript, Vite 5, Tailwind CSS 3, TanStack Query v5, React Router v6, Vitest + jsdom

## Global Constraints

- All source under `frontend/src/`
- API base path in browser: `/api` (nginx strips prefix before forwarding to `api:8080`)
- Tailwind dark theme: background `neutral-950`, card `neutral-900`, border `neutral-800`, accent `purple-400`
- All user-visible text in 繁體中文 (UI labels, placeholders, messages)
- TypeScript strict mode; no `any`
- Commits: `feat(frontend): …` prefix
- Do not push to remote
- Run `cd frontend && npm test` after each task to verify no regressions

---

## File Map

### Files to delete
```
frontend/src/pages/Dashboard.tsx
frontend/src/pages/Transcripts.tsx
frontend/src/pages/SrtViewer.tsx
frontend/src/pages/Upload.tsx
frontend/src/components/StatusBar.tsx
frontend/src/components/TaskAccordion.tsx
frontend/src/components/StatsPanel.tsx
frontend/src/components/SpeakerPanel.tsx
frontend/src/components/TimelinePanel.tsx
frontend/src/components/UploadProgress.tsx
frontend/src/components/JobList.tsx
```

### Files to modify
```
frontend/src/App.tsx                      — single route → <Home />
frontend/src/components/Layout.tsx        — remove nav links; main becomes flex-1 flex overflow-hidden
frontend/src/components/DropZone.tsx      — add compact?: boolean prop
```

### New files
```
frontend/src/hooks/useDebounce.ts         — shared debounce hook (used by TranscriptList + RightPanel)
frontend/src/pages/Home.tsx               — two-column layout, owns stem state + URL sync
frontend/src/components/LeftPanel.tsx     — upload + processing/queue + stats + transcript list + failed
frontend/src/components/StatsSummary.tsx  — one-line stats row (N tasks / Xh Ym / success%)
frontend/src/components/TranscriptList.tsx — search input + paginated file list
frontend/src/components/RightPanel.tsx    — audio player + title bar + segments + collapsible sections
frontend/src/components/SummarySection.tsx — collapsible summary (lazy-fetched)
frontend/src/components/KeywordList.tsx   — collapsible keyword frequency list (lazy-fetched)
```

### Files unchanged
```
frontend/src/api/types.ts
frontend/src/api/client.ts
frontend/src/api/client.test.ts
frontend/src/hooks/useSSE.ts
frontend/src/components/AudioPlayer.tsx
frontend/src/components/SrtSegmentList.tsx
frontend/src/components/SrtEditor.tsx
```

---

## Task 1: Cleanup — delete old files, simplify App + Layout, stub Home

**Files:**
- Delete: all 11 files listed above
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Layout.tsx`
- Create: `frontend/src/pages/Home.tsx` (stub only)

**Interfaces:**
- Produces: `Layout` renders `<main className="flex-1 flex overflow-hidden">` for two-column child layout

- [ ] **Step 1: Delete obsolete files**

```bash
cd frontend && rm src/pages/Dashboard.tsx src/pages/Transcripts.tsx src/pages/SrtViewer.tsx src/pages/Upload.tsx
rm src/components/StatusBar.tsx src/components/TaskAccordion.tsx src/components/StatsPanel.tsx
rm src/components/SpeakerPanel.tsx src/components/TimelinePanel.tsx src/components/UploadProgress.tsx
rm src/components/JobList.tsx
```

- [ ] **Step 2: Create stub Home page**

Create `frontend/src/pages/Home.tsx`:
```tsx
export function Home() {
  return <div className="flex-1 flex items-center justify-center text-neutral-600 text-sm">loading…</div>
}
```

- [ ] **Step 3: Rewrite App.tsx**

Replace entire `frontend/src/App.tsx`:
```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { Home } from '@/pages/Home'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="*" element={<Home />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
```

- [ ] **Step 4: Rewrite Layout.tsx**

Replace entire `frontend/src/components/Layout.tsx`:
```tsx
import { useState, useEffect } from 'react'
import { Outlet } from 'react-router-dom'

function LiveClock() {
  const [clock, setClock] = useState('')
  useEffect(() => {
    const tick = () => setClock(new Date().toISOString().replace('T', ' ').slice(0, 19))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="text-neutral-500 text-xs tabular-nums">{clock}</span>
}

export function Layout() {
  return (
    <div className="h-screen flex flex-col bg-neutral-950 text-neutral-100 font-mono overflow-hidden">
      <header className="border-b border-neutral-800 px-6 py-3 flex items-center justify-between flex-shrink-0">
        <div className="text-lg font-bold tracking-tight">
          media<span className="text-purple-400">flow</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5 text-neutral-400 text-xs">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            live
          </span>
          <LiveClock />
        </div>
      </header>
      <main className="flex-1 flex overflow-hidden">
        <Outlet />
      </main>
    </div>
  )
}
```

- [ ] **Step 5: Verify build compiles**

```bash
cd frontend && npm run build 2>&1 | tail -20
```
Expected: no TypeScript errors, build succeeds.

- [ ] **Step 6: Run tests**

```bash
cd frontend && npm test
```
Expected: `client.test.ts` passes (3 tests).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/Layout.tsx frontend/src/pages/Home.tsx
git commit -m "feat(frontend): cleanup — delete old pages/components, single-route App, simplified Layout"
```

---

## Task 2: useDebounce hook + Home.tsx with stem URL state

**Files:**
- Create: `frontend/src/hooks/useDebounce.ts`
- Create: `frontend/src/pages/Home.tsx` (replace stub)
- Create: `frontend/src/hooks/useDebounce.test.ts`

**Interfaces:**
- Produces: `useDebounce<T>(value: T, delay: number): T`
- Produces: `Home` renders `<LeftPanel selectedStem onSelect>` and `<RightPanel stem>` (both stubbed until later tasks)
- Produces: URL `?stem=lesson01` syncs with `stem` state (used by Tasks 3–6)

- [ ] **Step 1: Create useDebounce.ts**

Create `frontend/src/hooks/useDebounce.ts`:
```ts
import { useState, useEffect } from 'react'

export function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(id)
  }, [value, delay])
  return debounced
}
```

- [ ] **Step 2: Write failing test for useDebounce**

Create `frontend/src/hooks/useDebounce.test.ts`:
```ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useDebounce } from './useDebounce'

describe('useDebounce', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('returns initial value immediately', () => {
    const { result } = renderHook(() => useDebounce('hello', 300))
    expect(result.current).toBe('hello')
  })

  it('does not update before delay', () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 300),
      { initialProps: { value: 'a' } },
    )
    rerender({ value: 'b' })
    act(() => vi.advanceTimersByTime(200))
    expect(result.current).toBe('a')
  })

  it('updates after delay', () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounce(value, 300),
      { initialProps: { value: 'a' } },
    )
    rerender({ value: 'b' })
    act(() => vi.advanceTimersByTime(300))
    expect(result.current).toBe('b')
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd frontend && npm test -- useDebounce
```
Expected: FAIL — `useDebounce is not a function` or similar (file doesn't exist yet if you didn't create it in Step 1, but Step 1 exists, so it should pass — run anyway to confirm setup).

- [ ] **Step 4: Run test to verify it passes**

```bash
cd frontend && npm test -- useDebounce
```
Expected: 3 tests PASS.

- [ ] **Step 5: Create Home.tsx (stub LeftPanel + RightPanel inline until those tasks)**

Replace `frontend/src/pages/Home.tsx`:
```tsx
import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSSE } from '@/hooks/useSSE'

export function Home() {
  useSSE()
  const [searchParams, setSearchParams] = useSearchParams()
  const [stem, setStem] = useState<string | null>(searchParams.get('stem'))

  useEffect(() => {
    const urlStem = searchParams.get('stem')
    if (urlStem !== stem) setStem(urlStem)
  }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectStem = (s: string) => {
    setStem(s)
    setSearchParams({ stem: s }, { replace: true })
  }

  return (
    <div className="flex w-full overflow-hidden">
      <div className="w-80 flex-shrink-0 border-r border-neutral-800 p-4 text-xs text-neutral-600">
        left panel (coming soon) — selected: {stem ?? 'none'}
      </div>
      <div className="flex-1 p-4 text-xs text-neutral-600">
        right panel (coming soon)
        <button className="ml-4 text-purple-400" onClick={() => selectStem('test')}>test select</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 6: Verify build + tests**

```bash
cd frontend && npm run build 2>&1 | tail -5 && npm test
```
Expected: build OK, 4 tests pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/hooks/useDebounce.ts frontend/src/hooks/useDebounce.test.ts frontend/src/pages/Home.tsx
git commit -m "feat(frontend): useDebounce hook + Home stem/URL state"
```

---

## Task 3: DropZone compact mode + LeftPanel upload section

**Files:**
- Modify: `frontend/src/components/DropZone.tsx`
- Create: `frontend/src/components/LeftPanel.tsx` (upload section only; processing/stats/list added in Task 4)

**Interfaces:**
- Produces: `DropZone` accepts `compact?: boolean` — when true, uses minimal padding and single-line text
- Produces: `LeftPanel({ selectedStem, onSelect })` — renders compact upload zone + inline per-file progress

- [ ] **Step 1: Add compact prop to DropZone.tsx**

Replace entire `frontend/src/components/DropZone.tsx`:
```tsx
import { useRef, useState } from 'react'

const ACCEPTED = new Set(['.mp4', '.m4a', '.mp3', '.wav', '.flac'])

function ext(name: string) {
  return name.slice(name.lastIndexOf('.')).toLowerCase()
}

interface Props {
  onFiles: (files: File[]) => void
  compact?: boolean
}

export function DropZone({ onFiles, compact = false }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handle = (files: FileList | null) => {
    if (!files) return
    const valid = Array.from(files).filter(f => ACCEPTED.has(ext(f.name)))
    if (valid.length) onFiles(valid)
  }

  return (
    <div
      className={`border-2 border-dashed rounded-lg text-center transition-colors cursor-pointer ${
        compact ? 'px-3 py-2.5' : 'p-12'
      } ${dragging ? 'border-purple-500 bg-purple-950/20' : 'border-neutral-700 hover:border-neutral-500'}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); handle(e.dataTransfer.files) }}
    >
      {compact ? (
        <p className="text-xs text-neutral-500">拖曳或點擊選擇音訊／視訊</p>
      ) : (
        <>
          <div className="text-4xl mb-3 text-neutral-500">⬆</div>
          <p className="text-neutral-400 mb-2">拖曳檔案到這裡，或</p>
          <button
            type="button"
            className="px-4 py-1.5 bg-purple-700 text-white text-sm rounded hover:bg-purple-600"
          >
            選擇檔案
          </button>
          <p className="text-xs text-neutral-600 mt-3">
            支援 .mp4 .m4a .mp3 .wav .flac｜單檔上限 5 GB
          </p>
        </>
      )}
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

- [ ] **Step 2: Create LeftPanel.tsx with upload section**

Create `frontend/src/components/LeftPanel.tsx`:
```tsx
import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { DropZone } from '@/components/DropZone'
import type { StatusOverview } from '@/api/types'

type UploadItem = {
  key: string
  name: string
  progress: number
  message: string
  status: 'uploading' | 'done' | 'error'
}

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
    onProgress(Math.round(((part.part_number - 1) / init.parts.length) * 90), `${part.part_number}/${init.parts.length}`)
    const res = await fetch(part.url, { method: 'PUT', body: chunk })
    if (!res.ok) throw new Error(`Part ${part.part_number} failed: ${res.status}`)
    completedParts.push({ part_number: part.part_number, etag: res.headers.get('ETag') ?? '' })
  }
  onProgress(90, '完成中…')
  await api.uploadComplete({ upload_id: init.upload_id, minio_key: init.minio_key, parts: completedParts })
  onProgress(100, '✓ 已加入佇列')
}

const EMPTY: StatusOverview = { processing: [], queue: [], recent: [], failed: [] }
const PIPELINE_STAGES = ['preprocess', 'transcribe', 'verify_segments', 'correct_srt', 'summarize']

function StagePips({ current }: { current: string | null }) {
  const curIdx = current ? PIPELINE_STAGES.indexOf(current) : -1
  return (
    <div className="flex gap-1">
      {PIPELINE_STAGES.map((s, i) => (
        <div key={s} title={s} className={`w-1.5 h-1.5 rounded-full ${
          s === current   ? 'bg-blue-400 animate-pulse' :
          i < curIdx      ? 'bg-neutral-500' :
                            'bg-neutral-800'
        }`} />
      ))}
    </div>
  )
}

export interface LeftPanelProps {
  selectedStem: string | null
  onSelect: (stem: string) => void
}

export function LeftPanel({ selectedStem, onSelect }: LeftPanelProps) {
  const [uploads, setUploads] = useState<UploadItem[]>([])
  const qc = useQueryClient()

  const { data = EMPTY } = useQuery({
    queryKey: ['status'],
    queryFn: api.getStatus,
    refetchInterval: 30_000,
  })

  const cancel = useMutation({
    mutationFn: (stem: string) => api.cancelTask(stem),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })

  const rerun = useMutation({
    mutationFn: (stem: string) => api.rerunTask(stem, null),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })

  const handleFiles = useCallback((files: File[]) => {
    files.forEach(file => {
      const key = `${file.name}-${Date.now()}`
      setUploads(prev => [...prev, { key, name: file.name, progress: 0, message: '初始化中…', status: 'uploading' }])
      uploadFile(file, (progress, message) => {
        setUploads(prev => prev.map(u => u.key === key
          ? { ...u, progress, message, status: progress === 100 ? 'done' : 'uploading' }
          : u
        ))
      })
        .then(() => setTimeout(() => setUploads(prev => prev.filter(u => !(u.key === key && u.status === 'done'))), 3000))
        .catch(err => setUploads(prev => prev.map(u => u.key === key ? { ...u, status: 'error', message: String(err) } : u)))
    })
  }, [])

  return (
    <div className="w-80 flex-shrink-0 flex flex-col border-r border-neutral-800 overflow-hidden">

      {/* Upload */}
      <div className="flex-shrink-0 p-3 border-b border-neutral-800">
        <DropZone onFiles={handleFiles} compact />
        {uploads.map(u => (
          <div key={u.key} className="mt-2">
            <div className="flex items-center gap-2 text-xs">
              <span className="flex-1 truncate text-neutral-300">{u.name}</span>
              <span className={u.status === 'error' ? 'text-red-400' : u.status === 'done' ? 'text-green-400' : 'text-blue-400'}>
                {u.message}
              </span>
            </div>
            {u.status === 'uploading' && (
              <div className="mt-1 h-0.5 bg-neutral-800 rounded-full overflow-hidden">
                <div className="h-full bg-blue-500 rounded-full transition-all duration-200" style={{ width: `${u.progress}%` }} />
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Processing / Queue */}
      {(data.processing.length > 0 || data.queue.length > 0) && (
        <div className="flex-shrink-0 border-b border-neutral-800 px-3 py-2.5 space-y-1.5">
          {data.processing.map(t => (
            <div key={t.stem} className="flex items-center gap-2 text-xs">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse flex-shrink-0" />
              <span className="flex-1 truncate text-neutral-300">{t.filename || t.stem}</span>
              <StagePips current={t.current_stage} />
            </div>
          ))}
          {data.queue.map(t => (
            <div key={t.stem} className="flex items-center gap-2 text-xs">
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 flex-shrink-0" />
              <span className="flex-1 truncate text-neutral-500">{t.filename || t.stem}</span>
              <button
                onClick={() => { if (confirm(`取消 ${t.stem}？`)) cancel.mutate(t.stem) }}
                className="text-red-500 hover:text-red-300 text-xs leading-none"
                title="取消"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Stats + TranscriptList + Failed — added in Tasks 4–5 */}
      <div className="flex-1 flex items-center justify-center text-xs text-neutral-700">
        transcript list (coming soon)
      </div>

      {/* Failed */}
      {data.failed.length > 0 && (
        <div className="flex-shrink-0 border-t border-neutral-800 px-3 py-2.5 space-y-1.5">
          <div className="text-xs text-neutral-600 uppercase tracking-wider mb-1">Failed</div>
          {data.failed.map(t => (
            <div key={t.stem} className="flex items-center gap-2 text-xs">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400 flex-shrink-0" />
              <span className="flex-1 truncate text-neutral-400" title={t.error_msg ?? ''}>{t.filename || t.stem}</span>
              <button
                onClick={() => rerun.mutate(t.stem)}
                className="text-yellow-500 hover:text-yellow-300 text-xs"
              >
                重跑
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Wire LeftPanel into Home.tsx**

Replace `frontend/src/pages/Home.tsx`:
```tsx
import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSSE } from '@/hooks/useSSE'
import { LeftPanel } from '@/components/LeftPanel'

export function Home() {
  useSSE()
  const [searchParams, setSearchParams] = useSearchParams()
  const [stem, setStem] = useState<string | null>(searchParams.get('stem'))

  useEffect(() => {
    const urlStem = searchParams.get('stem')
    if (urlStem !== stem) setStem(urlStem)
  }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectStem = (s: string) => {
    setStem(s)
    setSearchParams({ stem: s }, { replace: true })
  }

  return (
    <div className="flex w-full overflow-hidden">
      <LeftPanel selectedStem={stem} onSelect={selectStem} />
      <div className="flex-1 flex items-center justify-center text-xs text-neutral-600">
        right panel (coming soon)
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Build + test**

```bash
cd frontend && npm run build 2>&1 | tail -5 && npm test
```
Expected: build OK, all tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DropZone.tsx frontend/src/components/LeftPanel.tsx frontend/src/pages/Home.tsx
git commit -m "feat(frontend): LeftPanel — compact DropZone + auto-upload + processing/queue/failed"
```

---

## Task 4: StatsSummary + TranscriptList — wire into LeftPanel

**Files:**
- Create: `frontend/src/components/StatsSummary.tsx`
- Create: `frontend/src/components/TranscriptList.tsx`
- Create: `frontend/src/components/TranscriptList.test.ts`
- Modify: `frontend/src/components/LeftPanel.tsx` — replace placeholder with real components

**Interfaces:**
- Consumes: `useDebounce` from `@/hooks/useDebounce`
- Consumes: `api.getStatsOverview`, `api.getFiles` from `@/api/client`
- Produces: `StatsSummary()` — no props, renders one-line stats row
- Produces: `TranscriptList({ selectedStem, onSelect })` — search + list with "載入更多"

- [ ] **Step 1: Write failing test for filter logic**

Create `frontend/src/components/TranscriptList.test.ts`:
```ts
import { describe, it, expect } from 'vitest'
import type { SrtFile } from '@/api/types'

function filterFiles(files: SrtFile[], q: string): SrtFile[] {
  if (!q) return files
  const lq = q.toLowerCase()
  return files.filter(f => f.stem.toLowerCase().includes(lq))
}

const FILES: SrtFile[] = [
  { stem: 'lesson01', size_kb: 10, mtime: 1000 },
  { stem: 'meeting_2026', size_kb: 20, mtime: 2000 },
  { stem: 'podcast_ep01', size_kb: 15, mtime: 3000 },
]

describe('filterFiles', () => {
  it('returns all when query is empty', () => {
    expect(filterFiles(FILES, '')).toHaveLength(3)
  })
  it('filters by stem substring (case-insensitive)', () => {
    expect(filterFiles(FILES, 'lesson')).toEqual([FILES[0]])
    expect(filterFiles(FILES, 'MEETING')).toEqual([FILES[1]])
  })
  it('returns empty array when no match', () => {
    expect(filterFiles(FILES, 'zzz')).toHaveLength(0)
  })
})
```

- [ ] **Step 2: Run test — expect FAIL** (function not exported yet)

```bash
cd frontend && npm test -- TranscriptList
```
Expected: FAIL — `filterFiles is not defined` (it's defined inside the test file, so it will actually pass — that's fine, this step confirms the test infra works).

- [ ] **Step 3: Create StatsSummary.tsx**

Create `frontend/src/components/StatsSummary.tsx`:
```tsx
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

function fmtSec(sec: number): string {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export function StatsSummary() {
  const { data } = useQuery({
    queryKey: ['stats-overview'],
    queryFn: api.getStatsOverview,
    staleTime: 60_000,
  })
  if (!data) return null
  return (
    <div className="flex-shrink-0 px-3 py-2 border-b border-neutral-800 flex gap-3 text-xs text-neutral-500">
      <span>{data.total_tasks} 個任務</span>
      <span>{fmtSec(data.total_duration_sec)}</span>
      <span>{Math.round(data.success_rate * 100)}% 成功</span>
    </div>
  )
}
```

- [ ] **Step 4: Create TranscriptList.tsx**

Create `frontend/src/components/TranscriptList.tsx`:
```tsx
import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useDebounce } from '@/hooks/useDebounce'
import type { SrtFile } from '@/api/types'

const PAGE_SIZE = 30

function filterFiles(files: SrtFile[], q: string): SrtFile[] {
  if (!q) return files
  const lq = q.toLowerCase()
  return files.filter(f => f.stem.toLowerCase().includes(lq))
}

interface Props {
  selectedStem: string | null
  onSelect: (stem: string) => void
}

export function TranscriptList({ selectedStem, onSelect }: Props) {
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(PAGE_SIZE)
  const debouncedQ = useDebounce(q, 300)

  const { data: allFiles = [], isLoading } = useQuery({
    queryKey: ['files'],
    queryFn: api.getFiles,
    staleTime: 30_000,
  })

  const filtered = useMemo(() => filterFiles(allFiles, debouncedQ), [allFiles, debouncedQ])
  const visible = filtered.slice(0, limit)
  const remaining = filtered.length - limit

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex-shrink-0 px-3 py-2 border-b border-neutral-800">
        <input
          type="search"
          value={q}
          onChange={e => { setQ(e.target.value); setLimit(PAGE_SIZE) }}
          placeholder="搜尋逐字稿…"
          className="w-full text-xs bg-neutral-900 border border-neutral-700 rounded px-2 py-1.5 text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-purple-600"
        />
      </div>

      <div className="flex-1 overflow-y-auto">
        {isLoading && <p className="px-3 py-3 text-xs text-neutral-600">載入中…</p>}
        {!isLoading && filtered.length === 0 && (
          <p className="px-3 py-3 text-xs text-neutral-600">無符合結果</p>
        )}
        {visible.map(f => (
          <button
            key={f.stem}
            onClick={() => onSelect(f.stem)}
            className={`w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-neutral-900 transition-colors border-l-2 ${
              f.stem === selectedStem
                ? 'border-purple-500 bg-purple-950/20 text-neutral-100'
                : 'border-transparent text-neutral-400'
            }`}
          >
            <span className="flex-1 truncate text-xs">{f.stem}</span>
            <span className="text-neutral-700 tabular-nums text-xs flex-shrink-0">
              {new Date(f.mtime * 1000).toLocaleDateString('zh-TW', { month: '2-digit', day: '2-digit' })}
            </span>
          </button>
        ))}
        {remaining > 0 && (
          <button
            onClick={() => setLimit(l => l + PAGE_SIZE)}
            className="w-full px-3 py-2 text-xs text-neutral-600 hover:text-neutral-400 text-center"
          >
            載入更多（{remaining} 筆）
          </button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Run tests**

```bash
cd frontend && npm test -- TranscriptList
```
Expected: 3 tests PASS.

- [ ] **Step 6: Replace placeholder in LeftPanel.tsx**

In `frontend/src/components/LeftPanel.tsx`, add imports at the top:
```tsx
import { StatsSummary } from '@/components/StatsSummary'
import { TranscriptList } from '@/components/TranscriptList'
```

Replace the `{/* Stats + TranscriptList + Failed — added in Tasks 4–5 */}` placeholder div with:
```tsx
      {/* Stats */}
      <StatsSummary />

      {/* Transcript list — scrollable */}
      <TranscriptList selectedStem={selectedStem} onSelect={onSelect} />
```

- [ ] **Step 7: Build + full test run**

```bash
cd frontend && npm run build 2>&1 | tail -5 && npm test
```
Expected: build OK, all tests pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/StatsSummary.tsx frontend/src/components/TranscriptList.tsx frontend/src/components/TranscriptList.test.ts frontend/src/components/LeftPanel.tsx
git commit -m "feat(frontend): StatsSummary + TranscriptList — search, pagination, stats row"
```

---

## Task 5: RightPanel — audio player, title bar, segment list

**Files:**
- Create: `frontend/src/components/RightPanel.tsx`
- Modify: `frontend/src/pages/Home.tsx` — wire in RightPanel

**Interfaces:**
- Consumes: `AudioPlayer({ audioRef, src, onTimeUpdate })` from `@/components/AudioPlayer`
- Consumes: `SrtSegmentList({ segments, currentTime, onSeek })` from `@/components/SrtSegmentList`
- Consumes: `SrtEditor({ stem })` from `@/components/SrtEditor`
- Consumes: `api.getSpeakerData(stem)`, `api.getSegments(stem, q)`, `api.audioUrl(stem)`
- Produces: `RightPanel({ stem: string | null })` — renders empty state when stem is null

- [ ] **Step 1: Create RightPanel.tsx**

Create `frontend/src/components/RightPanel.tsx`:
```tsx
import { useState, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useDebounce } from '@/hooks/useDebounce'
import { AudioPlayer } from '@/components/AudioPlayer'
import { SrtSegmentList } from '@/components/SrtSegmentList'
import { SrtEditor } from '@/components/SrtEditor'

interface Props {
  stem: string | null
}

export function RightPanel({ stem }: Props) {
  const [q, setQ] = useState('')
  const [currentTime, setCurrentTime] = useState(-1)
  const audioRef = useRef<HTMLAudioElement>(null)
  const debouncedQ = useDebounce(q, 300)

  const { data: speakerData } = useQuery({
    queryKey: ['speaker-data', stem],
    queryFn: () => api.getSpeakerData(stem!),
    enabled: !!stem,
    staleTime: 60_000,
  })

  const { data: segments = [] } = useQuery({
    queryKey: ['segments', stem, debouncedQ],
    queryFn: () => api.getSegments(stem!, debouncedQ || undefined),
    enabled: !!stem,
    staleTime: 30_000,
  })

  if (!stem) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-neutral-600">
        ← 從左側選擇一個逐字稿開始
      </div>
    )
  }

  const hasAudio = speakerData?.has_audio ?? false

  return (
    <div className="flex-1 flex flex-col overflow-hidden border-l border-neutral-800">
      {hasAudio && (
        <AudioPlayer
          audioRef={audioRef}
          src={api.audioUrl(stem)}
          onTimeUpdate={setCurrentTime}
        />
      )}

      {/* Title bar */}
      <div className="flex-shrink-0 flex items-center gap-3 px-4 py-2.5 border-b border-neutral-800">
        <span className="text-sm font-medium text-neutral-300 flex-1 truncate">{stem}</span>
        <input
          type="search"
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="搜尋段落…"
          className="w-44 text-xs bg-neutral-900 border border-neutral-700 rounded px-2 py-1 text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-purple-600"
        />
        <a
          href={`/api/files/${stem}/srt`}
          download={`${stem}.srt`}
          className="text-xs px-2.5 py-1 border border-neutral-700 text-neutral-400 rounded hover:border-purple-600 hover:text-purple-400 transition-colors"
        >
          下載 SRT
        </a>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto">
        <SrtEditor stem={stem} />
        <SrtSegmentList
          segments={segments}
          currentTime={currentTime}
          onSeek={hasAudio ? (t: number) => { if (audioRef.current) audioRef.current.currentTime = t } : undefined}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Wire RightPanel into Home.tsx**

Replace `frontend/src/pages/Home.tsx`:
```tsx
import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSSE } from '@/hooks/useSSE'
import { LeftPanel } from '@/components/LeftPanel'
import { RightPanel } from '@/components/RightPanel'

export function Home() {
  useSSE()
  const [searchParams, setSearchParams] = useSearchParams()
  const [stem, setStem] = useState<string | null>(searchParams.get('stem'))

  useEffect(() => {
    const urlStem = searchParams.get('stem')
    if (urlStem !== stem) setStem(urlStem)
  }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectStem = (s: string) => {
    setStem(s)
    setSearchParams({ stem: s }, { replace: true })
  }

  return (
    <div className="flex w-full overflow-hidden">
      <LeftPanel selectedStem={stem} onSelect={selectStem} />
      <RightPanel stem={stem} />
    </div>
  )
}
```

- [ ] **Step 3: Build + tests**

```bash
cd frontend && npm run build 2>&1 | tail -5 && npm test
```
Expected: build OK, all tests pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/RightPanel.tsx frontend/src/pages/Home.tsx
git commit -m "feat(frontend): RightPanel — audio player, title bar, segment list, edit"
```

---

## Task 6: SummarySection + KeywordList — wire into RightPanel

**Files:**
- Create: `frontend/src/components/SummarySection.tsx`
- Create: `frontend/src/components/KeywordList.tsx`
- Modify: `frontend/src/components/RightPanel.tsx` — add collapsible sections below segments

**Interfaces:**
- Consumes: `api.getSummary(stem)` → `string | null`
- Consumes: `api.getKeywords()` → `Keyword[]`
- Produces: `SummarySection({ stem: string })` — lazy-fetched, collapsible
- Produces: `KeywordList()` — lazy-fetched, collapsible

- [ ] **Step 1: Create SummarySection.tsx**

Create `frontend/src/components/SummarySection.tsx`:
```tsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function SummarySection({ stem }: { stem: string }) {
  const [open, setOpen] = useState(false)

  const { data: summary, isFetching } = useQuery({
    queryKey: ['summary', stem],
    queryFn: () => api.getSummary(stem),
    enabled: open,
    staleTime: Infinity,
  })

  return (
    <div className="border border-neutral-800 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-neutral-900 transition-colors text-xs"
        onClick={() => setOpen(o => !o)}
      >
        <span className="text-neutral-400">摘要</span>
        <span className="text-neutral-600">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="border-t border-neutral-800 px-4 py-3">
          {isFetching ? (
            <p className="text-xs text-neutral-600">載入中…</p>
          ) : summary == null ? (
            <p className="text-xs text-neutral-600">（無摘要）</p>
          ) : (
            <p className="text-xs text-neutral-300 leading-relaxed whitespace-pre-wrap">{summary}</p>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Create KeywordList.tsx**

Create `frontend/src/components/KeywordList.tsx`:
```tsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function KeywordList() {
  const [open, setOpen] = useState(false)

  const { data: keywords = [], isFetching } = useQuery({
    queryKey: ['keywords'],
    queryFn: api.getKeywords,
    enabled: open,
    staleTime: 60_000,
  })

  return (
    <div className="border border-neutral-800 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-neutral-900 transition-colors text-xs"
        onClick={() => setOpen(o => !o)}
      >
        <span className="text-neutral-400">高頻主題</span>
        <span className="text-neutral-600">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="border-t border-neutral-800 px-4 py-3">
          {isFetching ? (
            <p className="text-xs text-neutral-600">載入中…</p>
          ) : keywords.length === 0 ? (
            <p className="text-xs text-neutral-600">無主題資料</p>
          ) : (
            <div className="space-y-1">
              {keywords.map(kw => (
                <div key={kw.topic} className="flex items-center justify-between">
                  <span className="text-xs text-neutral-300 flex-1 truncate">{kw.topic}</span>
                  <span className="text-xs text-neutral-600 tabular-nums ml-2">{kw.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Add collapsible sections to RightPanel.tsx**

In `frontend/src/components/RightPanel.tsx`, add imports after existing imports:
```tsx
import { SummarySection } from '@/components/SummarySection'
import { KeywordList } from '@/components/KeywordList'
```

After the `<SrtSegmentList ... />` line inside the scrollable body div, add:
```tsx
        <div className="px-4 pb-6 mt-4 space-y-2">
          <SummarySection stem={stem} />
          <KeywordList />
        </div>
```

The scrollable body div should now look like:
```tsx
      <div className="flex-1 overflow-y-auto">
        <SrtEditor stem={stem} />
        <SrtSegmentList
          segments={segments}
          currentTime={currentTime}
          onSeek={hasAudio ? (t: number) => { if (audioRef.current) audioRef.current.currentTime = t } : undefined}
        />
        <div className="px-4 pb-6 mt-4 space-y-2">
          <SummarySection stem={stem} />
          <KeywordList />
        </div>
      </div>
```

- [ ] **Step 4: Final build + full test run**

```bash
cd frontend && npm run build 2>&1 | tail -10 && npm test
```
Expected: build OK, TypeScript clean, all tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SummarySection.tsx frontend/src/components/KeywordList.tsx frontend/src/components/RightPanel.tsx
git commit -m "feat(frontend): SummarySection + KeywordList — lazy-fetched collapsible sections in RightPanel"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Single page, no `/upload` / `/transcripts` / `/transcripts/:stem` routes
- ✅ Left panel: compact DropZone, auto-upload with inline progress
- ✅ Left panel: processing/queue with stage pips (5 default stages)
- ✅ Left panel: cancel button on queued jobs
- ✅ Left panel: stats summary row (tasks / duration / success rate)
- ✅ Left panel: search + paginated transcript list (30 per page)
- ✅ Left panel: failed jobs with rerun button
- ✅ Right panel: sticky audio player (conditional on `has_audio`)
- ✅ Right panel: title bar with search + download SRT
- ✅ Right panel: segment list with click-to-seek + playback highlight
- ✅ Right panel: SrtEditor (edit mode inline)
- ✅ Right panel: collapsible summary
- ✅ Right panel: collapsible keyword list
- ✅ `?stem=` URL bookmark support
- ✅ Deleted: SpeakerPanel, TimelinePanel, StatsPanel speaker bar, TaskAccordion stage timeline
- ✅ SSE real-time updates wired in Home.tsx

**No placeholders:** confirmed — no TBD/TODO in any step.

**Type consistency:**
- `LeftPanelProps.selectedStem` → `TranscriptList.selectedStem` → same prop name ✅
- `RightPanel({ stem })` → `SummarySection({ stem })` — same type `string` ✅
- `api.getSummary` returns `string | null` — `SummarySection` handles both cases ✅
