import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { DropZone } from '@/components/DropZone'
import { StatsSummary } from '@/components/StatsSummary'
import { TranscriptList } from '@/components/TranscriptList'
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
        <DropZone onFiles={handleFiles} />
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

      {/* Stats */}
      <StatsSummary />

      {/* Transcript list — scrollable */}
      <TranscriptList selectedStem={selectedStem} onSelect={onSelect} />

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
