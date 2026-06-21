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
