import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

function fmtSec(sec: number): string {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export function StatsSummary() {
  const { data } = useQuery({
    queryKey: ['stats-overview'],
    queryFn: api.getStatsOverview,
    staleTime: 60_000,
  })
  if (!data) return null
  return (
    <div className="flex-shrink-0 px-3 py-2 border-b border-neutral-800 flex gap-3 text-xs text-neutral-500">
      <span>{data.total_tasks} 個任務</span>
      <span>{fmtSec(data.total_duration_sec)}</span>
      <span>{Math.round(data.success_rate * 100)}% 成功</span>
    </div>
  )
}
