import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function SummarySection({ stem }: { stem: string }) {
  const [open, setOpen] = useState(false)

  const { data: summary, isFetching } = useQuery({
    queryKey: ['summary', stem],
    queryFn: () => api.getSummary(stem),
    enabled: open,
    staleTime: Infinity,
  })

  return (
    <div className="border border-neutral-800 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-neutral-900 transition-colors text-xs"
        onClick={() => setOpen(o => !o)}
      >
        <span className="text-neutral-400">摘要</span>
        <span className="text-neutral-600">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="border-t border-neutral-800 px-4 py-3">
          {isFetching ? (
            <p className="text-xs text-neutral-600">載入中…</p>
          ) : summary == null ? (
            <p className="text-xs text-neutral-600">（無摘要）</p>
          ) : (
            <p className="text-xs text-neutral-300 leading-relaxed whitespace-pre-wrap">{summary}</p>
          )}
        </div>
      )}
    </div>
  )
}
