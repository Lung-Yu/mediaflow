import { useState } from 'react'
import type { TaskTimeline } from '@/api/types'

function fmtDur(sec: number | null) {
  if (!sec) return '—'
  return sec >= 60 ? `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s` : `${Math.floor(sec)}s`
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
                  className="h-full bg-purple-400 rounded-full"
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
