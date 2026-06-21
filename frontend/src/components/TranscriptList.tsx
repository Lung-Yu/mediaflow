import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useDebounce } from '@/hooks/useDebounce'
import type { SrtFile } from '@/api/types'

const PAGE_SIZE = 30

function filterFiles(files: SrtFile[], q: string): SrtFile[] {
  if (!q) return files
  const lq = q.toLowerCase()
  return files.filter(f => f.stem.toLowerCase().includes(lq))
}

interface Props {
  selectedStem: string | null
  onSelect: (stem: string) => void
}

export function TranscriptList({ selectedStem, onSelect }: Props) {
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(PAGE_SIZE)
  const debouncedQ = useDebounce(q, 300)

  const { data: allFiles = [], isLoading } = useQuery({
    queryKey: ['files'],
    queryFn: api.getFiles,
    staleTime: 30_000,
  })

  const filtered = useMemo(() => filterFiles(allFiles, debouncedQ), [allFiles, debouncedQ])
  const visible = filtered.slice(0, limit)
  const remaining = filtered.length - limit

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex-shrink-0 px-3 py-2 border-b border-neutral-800">
        <input
          type="search"
          value={q}
          onChange={e => { setQ(e.target.value); setLimit(PAGE_SIZE) }}
          placeholder="搜尋逐字稿…"
          className="w-full text-xs bg-neutral-900 border border-neutral-700 rounded px-2 py-1.5 text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-purple-600"
        />
      </div>

      <div className="flex-1 overflow-y-auto">
        {isLoading && <p className="px-3 py-3 text-xs text-neutral-600">載入中…</p>}
        {!isLoading && filtered.length === 0 && (
          <p className="px-3 py-3 text-xs text-neutral-600">無符合結果</p>
        )}
        {visible.map(f => (
          <button
            key={f.stem}
            onClick={() => onSelect(f.stem)}
            className={`w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-neutral-900 transition-colors border-l-2 ${
              f.stem === selectedStem
                ? 'border-purple-500 bg-purple-950/20 text-neutral-100'
                : 'border-transparent text-neutral-400'
            }`}
          >
            <span className="flex-1 truncate text-xs">{f.stem}</span>
            <span className="text-neutral-700 tabular-nums text-xs flex-shrink-0">
              {new Date(f.mtime * 1000).toLocaleDateString('zh-TW', { month: '2-digit', day: '2-digit' })}
            </span>
          </button>
        ))}
        {remaining > 0 && (
          <button
            onClick={() => setLimit(l => l + PAGE_SIZE)}
            className="w-full px-3 py-2 text-xs text-neutral-600 hover:text-neutral-400 text-center"
          >
            載入更多（{remaining} 筆）
          </button>
        )}
      </div>
    </div>
  )
}
