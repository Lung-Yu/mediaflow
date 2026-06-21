import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function KeywordList() {
  const [open, setOpen] = useState(false)

  const { data: keywords = [], isFetching } = useQuery({
    queryKey: ['keywords'],
    queryFn: api.getKeywords,
    enabled: open,
    staleTime: 60_000,
  })

  return (
    <div className="border border-neutral-800 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-neutral-900 transition-colors text-xs"
        onClick={() => setOpen(o => !o)}
      >
        <span className="text-neutral-400">高頻主題</span>
        <span className="text-neutral-600">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="border-t border-neutral-800 px-4 py-3">
          {isFetching ? (
            <p className="text-xs text-neutral-600">載入中…</p>
          ) : keywords.length === 0 ? (
            <p className="text-xs text-neutral-600">無主題資料</p>
          ) : (
            <div className="space-y-1">
              {keywords.map(kw => (
                <div key={kw.topic} className="flex items-center justify-between">
                  <span className="text-xs text-neutral-300 flex-1 truncate">{kw.topic}</span>
                  <span className="text-xs text-neutral-600 tabular-nums ml-2">{kw.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
