import type {
  StatusOverview, SrtFile, Segment, SpeakerData,
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

async function json<T>(method: string, path: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

async function del(path: string): Promise<void> {
  await fetch(BASE + path, { method: 'DELETE' })
}

export const api = {
  getStatus:        () => get<StatusOverview>('/status/'),
  getFiles:         () => get<SrtFile[]>('/files/'),
  getSegments:      (stem: string, q?: string) =>
                      get<Segment[]>(`/files/${stem}/segments`, q ? { q } : undefined),
  getSpeakerData:   (stem: string) => get<SpeakerData>(`/files/${stem}/speaker-names`),
  getSummary:       (stem: string): Promise<string | null> =>
                      fetch(`${BASE}/files/${stem}/summary`).then(r => r.ok ? r.text() : null),
  saveSrt:          (stem: string, content: string) =>
                      json<{ saved: boolean; bytes: number }>('PUT', `/files/${stem}/srt`, { content }),
  getRawSrt:        (stem: string): Promise<string> =>
                      fetch(`${BASE}/files/${stem}/srt`).then(r => {
                        if (!r.ok) throw new Error(`GET /files/${stem}/srt → ${r.status}`)
                        return r.text()
                      }),
  getStatsOverview: () => get<StatsOverview>('/stats/overview'),
  getKeywords:      () => get<Keyword[]>('/stats/keywords'),
  uploadInit:       (req: UploadInitRequest) => json<UploadInitResponse>('POST', '/upload/init', req),
  uploadComplete:   (body: { upload_id: string; minio_key: string; parts: { part_number: number; etag: string }[] }) =>
                      json<{ stem: string; status: string }>('POST', '/upload/complete', body),
  rerunTask:        (stem: string, from_stage: string | null) =>
                      json<{ stem: string; status: string }>('POST', `/tasks/${stem}/runs`, { from_stage }),
  cancelTask:       (stem: string) => del(`/tasks/${stem}`),
  deleteFile:       (stem: string) => del(`/files/${stem}`),
  audioUrl:         (stem: string) => `${BASE}/files/${stem}/audio`,
  sseUrl:           () => `${BASE}/events/stream`,
}
