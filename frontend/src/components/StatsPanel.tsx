import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

function fmtSec(sec: number) {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export function StatsPanel() {
  const { data: overview } = useQuery({
    queryKey: ['stats-overview'],
    queryFn: api.getStatsOverview,
    staleTime: 60_000,
  })
  const { data: keywords = [] } = useQuery({
    queryKey: ['keywords'],
    queryFn: api.getKeywords,
    staleTime: 60_000,
  })

  if (!overview) return null

  return (
    <div className="grid md:grid-cols-2 gap-4 mb-6">
      {/* Speaker bar chart */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4">
        <div className="text-xs text-neutral-500 uppercase tracking-wider mb-3">說話者分佈</div>
        {overview.speakers.length === 0 ? (
          <p className="text-xs text-neutral-600">無說話者資料（需開啟 diarize 階段）</p>
        ) : (
          <div className="space-y-2">
            {overview.speakers.map(sp => (
              <div key={sp.label} className="flex items-center gap-2">
                <span className="text-xs text-neutral-400 w-24 truncate">{sp.label}</span>
                <div className="flex-1 h-2 bg-neutral-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-purple-500 rounded-full"
                    style={{ width: `${sp.pct * 100}%` }}
                  />
                </div>
                <span className="text-xs text-neutral-500 tabular-nums w-12 text-right">
                  {fmtSec(sp.seconds)}
                </span>
              </div>
            ))}
          </div>
        )}
        <div className="mt-3 pt-3 border-t border-neutral-800 flex gap-4 text-xs text-neutral-500">
          <span>{overview.total_tasks} 個任務</span>
          <span>{fmtSec(overview.total_duration_sec)} 總時長</span>
          <span>{Math.round(overview.success_rate * 100)}% 成功率</span>
        </div>
      </div>

      {/* Keyword frequency table */}
      <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4">
        <div className="text-xs text-neutral-500 uppercase tracking-wider mb-3">高頻主題</div>
        {keywords.length === 0 ? (
          <p className="text-xs text-neutral-600">無主題資料</p>
        ) : (
          <div className="space-y-1">
            {keywords.map(kw => (
              <div key={kw.topic} className="flex items-center justify-between">
                <span className="text-xs text-neutral-300 truncate flex-1">{kw.topic}</span>
                <span className="text-xs text-neutral-600 tabular-nums ml-2">{kw.count}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
