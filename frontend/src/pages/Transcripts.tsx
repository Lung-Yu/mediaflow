import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'

function fmtDate(mtime: number) {
  return new Date(mtime * 1000).toLocaleString('zh-TW', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

export function Transcripts() {
  const { data: files = [], isLoading } = useQuery({
    queryKey: ['files'],
    queryFn: api.getFiles,
    staleTime: 30_000,
  })

  return (
    <div>
      <div className="flex items-center gap-3 mb-5">
        <h1 className="text-base font-semibold text-neutral-200">Transcripts</h1>
        <span className="text-xs px-1.5 py-0.5 bg-neutral-800 text-neutral-400 rounded">
          {files.length}
        </span>
      </div>

      {isLoading ? (
        <p className="text-xs text-neutral-600">載入中…</p>
      ) : files.length === 0 ? (
        <p className="text-xs text-neutral-600">workspace/3_output/ 中沒有逐字稿檔案</p>
      ) : (
        <div className="space-y-1">
          {files.map(f => (
            <Link
              key={f.stem}
              to={`/transcripts/${f.stem}`}
              className="flex items-center gap-3 px-4 py-3 bg-neutral-900 border border-neutral-800 rounded-lg hover:border-purple-700 hover:bg-neutral-800 transition-colors"
            >
              <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
              <span className="flex-1 text-sm text-neutral-200">{f.stem}</span>
              <span className="text-xs text-neutral-600 tabular-nums">{f.size_kb} KB</span>
              <span className="text-xs text-neutral-600">{fmtDate(f.mtime)}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
