import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { Task } from '@/api/types'

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
  const stem = task.stem ?? task.id

  const { data: detail } = useQuery({
    queryKey: ['task-detail', stem],
    queryFn: async () => {
      const [timeline, summary, segments] = await Promise.all([
        api.getTimeline(stem),
        api.getSummary(stem),
        api.getSegments(stem),
      ])
      return { timeline, summary, segments: segments.slice(0, 3) }
    },
    enabled: open,
    staleTime: 60_000,
  })

  const rerun = useMutation({
    mutationFn: () => api.rerunTask(stem, rerunStage || null),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })

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
