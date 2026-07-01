import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { CorrectionSegment } from '@/api/types'

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

function parseHms(t: string): number {
  const [h, m, rest] = t.trim().split(':')
  const [s, ms] = rest.split(',')
  return +h * 3600 + +m * 60 + +s + +ms / 1000
}

export function tcToSeconds(tc: string): number {
  return parseHms(tc.split(' --> ')[0])
}

function toCorrection(
  parsed: { index: number; tc: string; text: string }[],
  edits: Record<number, string>,
): CorrectionSegment[] {
  return parsed.map(s => ({
    index: s.index,
    start: parseHms(s.tc.split(' --> ')[0]),
    end:   parseHms(s.tc.split(' --> ')[1]),
    text:  edits[s.index] ?? s.text,
  }))
}

export function SrtEditor({ stem, onSeek, savedOnce, setSavedOnce }: {
  stem: string
  onSeek?: (t: number) => void
  savedOnce: boolean
  setSavedOnce: (v: boolean) => void
}) {
  const [edits, setEdits] = useState<Record<number, string>>({})
  const qc = useQueryClient()

  const { data: rawSrt = '' } = useQuery({
    queryKey: ['raw-srt', stem],
    queryFn: () => api.getRawSrt(stem),
    staleTime: Infinity,
  })

  const parsed = parseSrt(rawSrt).filter(s => s.text.trim() !== '')

  const save = useMutation({
    mutationFn: () => api.saveCorrection(stem, toCorrection(parsed, edits)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['segments', stem] })
      qc.invalidateQueries({ queryKey: ['raw-srt', stem] })
      setEdits({})
      setSavedOnce(true)
    },
  })

  const finalize = useMutation({
    mutationFn: () => api.finalizeCorrection(stem),
  })

  const dirty = !!Object.keys(edits).length

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-2 border-b border-neutral-800 flex-shrink-0">
        <span className="text-xs text-neutral-500">
          直接點擊段落文字即可修改{dirty && <span className="ml-2 text-yellow-500">● 未儲存</span>}
          {finalize.isSuccess && <span className="ml-2 text-green-500">✓ 已確認</span>}
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
          {savedOnce && (
            <button
              onClick={() => finalize.mutate()}
              disabled={finalize.isPending || dirty}
              className="text-xs px-3 py-1 bg-green-800 text-white rounded hover:bg-green-700 disabled:opacity-40"
              title="標記此逐字稿已確認完成"
            >
              {finalize.isPending ? '確認中…' : '✓ 確認完成'}
            </button>
          )}
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
      {finalize.isError && (
        <div className="px-4 py-2 text-xs text-red-400 border-t border-red-900 flex-shrink-0">
          確認失敗 — {String(finalize.error)}
        </div>
      )}
    </div>
  )
}
