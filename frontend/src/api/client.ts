import type {
  StatusOverview, SrtFile, Segment, SpeakerData,
  StatsOverview, Keyword, UploadInitRequest, UploadInitResponse,
  UploadCompleteRequest, CorrectionSegment, JobEvent,
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
  uploadComplete:   (body: UploadCompleteRequest) =>
                      json<{ stem: string; status: string }>('POST', '/upload/complete', body),
  rerunTask:        (stem: string, _from_stage: string | null) =>
                      json<{ job_id: string; status: string }>('POST', `/jobs/${stem}/rerun`, null),
  cancelTask:       (stem: string) => del(`/jobs/${stem}`),
  deleteFile:       (stem: string) => del(`/files/${stem}`),
  saveCorrection:   (jobId: string, segments: CorrectionSegment[]) =>
                      json<void>('PATCH', `/jobs/${jobId}/correction`, { segments }),
  finalizeCorrection: (jobId: string) =>
                      json<void>('POST', `/jobs/${jobId}/correction/finalize`, null),
  getJobEvents:     (jobId: string) =>
                      get<JobEvent[]>(`/jobs/${jobId}/events`),
  audioUrl:         (stem: string) => `${BASE}/files/${stem}/audio`,
}
