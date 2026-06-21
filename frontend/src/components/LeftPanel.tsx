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
const STAGE_ZH: Record<string, string> = {
  preprocess:      '預處理',
  transcribe:      '轉錄中',
  verify_segments: '驗證中',
  correct_srt:     '校正中',
  summarize:       '摘要中',
  diarize:         '辨識說話者',
  detect_chapters: '分章節',
}

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
  const [cancelConfirm, setCancelConfirm] = useState<string | null>(null)
  const qc = useQueryClient()

  const { data = EMPTY } = useQuery({
    queryKey: ['status'],
    queryFn: api.getStatus,
    refetchInterval: 3_000,
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
        <div className="flex-shrink-0 border-b border-neutral-800 px-3 py-2.5 space-y-2">
          {data.processing.map(t => (
            <div key={t.stem} className="text-xs">
              <div className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse flex-shrink-0" />
                <span className="flex-1 truncate text-neutral-300">{t.filename || t.stem}</span>
                <span className="text-blue-400 flex-shrink-0">
                  {t.current_stage ? (STAGE_ZH[t.current_stage] ?? t.current_stage) : '處理中…'}
                </span>
              </div>
              <div className="ml-3.5 mt-1">
                <StagePips current={t.current_stage} />
              </div>
            </div>
          ))}
          {data.queue.map(t => (
            <div key={t.stem} className="flex items-center gap-2 text-xs">
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 flex-shrink-0" />
              <span className="flex-1 truncate text-neutral-500">{t.filename || t.stem}</span>
              {cancelConfirm === t.stem ? (
                <div className="flex items-center gap-1 flex-shrink-0">
                  <span className="text-neutral-500 text-xs">取消？</span>
                  <button onClick={() => { cancel.mutate(t.stem); setCancelConfirm(null) }} className="text-red-400 hover:text-red-300 text-xs px-1">✓</button>
                  <button onClick={() => setCancelConfirm(null)} className="text-neutral-600 hover:text-neutral-400 text-xs px-1">✕</button>
                </div>
              ) : (
                <>
                  <span className="text-neutral-600 flex-shrink-0">等待中</span>
                  <button
                    onClick={() => setCancelConfirm(t.stem)}
                    className="text-red-500 hover:text-red-300 text-xs leading-none flex-shrink-0"
                  >
                    ✕
                  </button>
                </>
              )}
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
            <div key={t.stem} className="text-xs space-y-0.5">
              <div className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-red-400 flex-shrink-0" />
                <span className="flex-1 truncate text-neutral-400">{t.filename || t.stem}</span>
                <button
                  onClick={() => rerun.mutate(t.stem)}
                  className="text-yellow-500 hover:text-yellow-300 flex-shrink-0"
                >
                  重跑
                </button>
              </div>
              {t.error_msg && (
                <p className="ml-3.5 text-red-400/70 break-all leading-tight">{t.error_msg}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
