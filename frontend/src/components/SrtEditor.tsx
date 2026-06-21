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

export function SrtEditor({ stem }: { stem: string }) {
  const [editMode, setEditMode] = useState(false)
  const [edits, setEdits] = useState<Record<number, string>>({})
  const qc = useQueryClient()

  const { data: rawSrt = '' } = useQuery({
    queryKey: ['raw-srt', stem],
    queryFn: () => api.getRawSrt(stem),
    enabled: editMode,
    staleTime: Infinity,
  })

  const parsed = parseSrt(rawSrt)

  const save = useMutation({
    mutationFn: () => {
      const updated = parsed.map(s => ({
        ...s,
        text: edits[s.index] ?? s.text,
      }))
      return api.saveSrt(stem, srtToText(updated))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['segments', stem] })
      qc.invalidateQueries({ queryKey: ['raw-srt', stem] })
      setEdits({})
      setEditMode(false)
    },
  })

  if (!editMode) {
    return (
      <div className="mb-4 flex justify-end">
        <button
          onClick={() => setEditMode(true)}
          className="text-xs px-3 py-1 border border-neutral-700 text-neutral-400 rounded hover:border-purple-600 hover:text-purple-400 transition-colors"
        >
          編輯逐字稿
        </button>
      </div>
    )
  }

  return (
    <div className="mb-4 border border-purple-800 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 bg-purple-950/30 border-b border-purple-800">
        <span className="text-xs text-purple-300">編輯模式 — 直接修改文字內容</span>
        <div className="flex gap-2">
          <button
            onClick={() => { setEditMode(false); setEdits({}) }}
            className="text-xs px-3 py-1 border border-neutral-700 text-neutral-400 rounded hover:bg-neutral-800"
          >
            取消
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending || Object.keys(edits).length === 0}
            className="text-xs px-3 py-1 bg-purple-700 text-white rounded hover:bg-purple-600 disabled:opacity-50"
          >
            {save.isPending ? '儲存中…' : '儲存'}
          </button>
        </div>
      </div>

      <div className="max-h-96 overflow-y-auto">
        {parsed.map(seg => (
          <div key={seg.index} className="flex gap-3 px-4 py-2 border-b border-neutral-800 last:border-0">
            <span className="text-xs text-neutral-600 tabular-nums w-16 flex-shrink-0 pt-1.5">
              {seg.tc.slice(0, 8)}
            </span>
            <textarea
              value={edits[seg.index] ?? seg.text}
              onChange={e => setEdits(ed => ({ ...ed, [seg.index]: e.target.value }))}
              rows={Math.max(1, (edits[seg.index] ?? seg.text).split('\n').length)}
              className="flex-1 text-sm bg-transparent text-neutral-200 resize-none focus:outline-none leading-relaxed"
            />
          </div>
        ))}
      </div>

      {save.isError && (
        <div className="px-4 py-2 text-xs text-red-400 border-t border-red-900">
          儲存失敗 — {String(save.error)}
        </div>
      )}
    </div>
  )
}
