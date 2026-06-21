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

function tcToSeconds(tc: string): number {
  const [h, m, rest] = tc.split(' --> ')[0].split(':')
  const [s, ms] = rest.split(',')
  return +h * 3600 + +m * 60 + +s + +ms / 1000
}

export function SrtEditor({ stem, onSeek }: { stem: string; onSeek?: (t: number) => void }) {
  const [edits, setEdits] = useState<Record<number, string>>({})
  const qc = useQueryClient()

  const { data: rawSrt = '' } = useQuery({
    queryKey: ['raw-srt', stem],
    queryFn: () => api.getRawSrt(stem),
    staleTime: Infinity,
  })

  const parsed = parseSrt(rawSrt).filter(s => s.text.trim() !== '')

  const save = useMutation({
    mutationFn: () => {
      const updated = parsed.map(s => ({ ...s, text: edits[s.index] ?? s.text }))
      return api.saveSrt(stem, srtToText(updated))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['segments', stem] })
      qc.invalidateQueries({ queryKey: ['raw-srt', stem] })
      setEdits({})
    },
  })

  const dirty = !!Object.keys(edits).length

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-2 border-b border-neutral-800 flex-shrink-0">
        <span className="text-xs text-neutral-500">
          直接點擊段落文字即可修改{dirty && <span className="ml-2 text-yellow-500">● 未儲存</span>}
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setEdits({})}
            disabled={!dirty}
            className="text-xs px-3 py-1 border border-neutral-700 text-neutral-400 rounded hover:bg-neutral-800 disabled:opacity-30"
          >
            還原
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending || !dirty}
            className="text-xs px-3 py-1 bg-purple-700 text-white rounded hover:bg-purple-600 disabled:opacity-40"
          >
            {save.isPending ? '儲存中…' : '儲存'}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {parsed.length === 0 ? (
          <p className="text-xs text-neutral-600 px-4 py-4">載入中…</p>
        ) : parsed.map(seg => (
          <div
            key={seg.index}
            className="flex gap-3 px-4 py-2 border-b border-neutral-800/60 last:border-0 hover:bg-neutral-800/30 focus-within:bg-neutral-900 focus-within:border-l-2 focus-within:border-purple-600 transition-colors"
          >
            <span
              className={`text-xs tabular-nums w-16 flex-shrink-0 pt-1.5 select-none ${onSeek ? 'cursor-pointer text-neutral-600 hover:text-purple-400 transition-colors' : 'text-neutral-600'}`}
              onClick={() => onSeek?.(tcToSeconds(seg.tc))}
              title={onSeek ? '點擊跳到此片段' : undefined}
            >
              {seg.tc.slice(0, 8)}
            </span>
            <textarea
              value={edits[seg.index] ?? seg.text}
              onChange={e => setEdits(ed => ({ ...ed, [seg.index]: e.target.value }))}
              rows={Math.max(1, (edits[seg.index] ?? seg.text).split('\n').length)}
              className="flex-1 text-sm bg-transparent text-neutral-200 resize-none outline-none leading-relaxed cursor-text"
            />
          </div>
        ))}
      </div>

      {save.isError && (
        <div className="px-4 py-2 text-xs text-red-400 border-t border-red-900 flex-shrink-0">
          儲存失敗 — {String(save.error)}
        </div>
      )}
    </div>
  )
}
