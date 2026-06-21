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
