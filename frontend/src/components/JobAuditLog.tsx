import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { JobEvent } from '@/api/types'

const STATUS_ICON: Record<string, string> = {
  success: '✓',
  failed:  '✗',
  started: '…',
}
const STATUS_COLOR: Record<string, string> = {
  success: 'text-neutral-300',
  failed:  'text-red-400',
  started: 'text-neutral-500',
}

export function JobAuditLog({ stem }: { stem: string }) {
  const { data: events = [], isLoading } = useQuery({
    queryKey: ['events', stem],
    queryFn: () => api.getJobEvents(stem),
    staleTime: 30_000,
  })

  if (isLoading) return <p className="text-xs text-neutral-600 px-4 py-4">載入中…</p>
  if (!events.length)  return <p className="text-xs text-neutral-600 px-4 py-4">無紀錄</p>

  return (
    <div className="text-xs">
      {events.map((ev: JobEvent, i: number) => (
        <div
          key={i}
          className={`flex gap-2 items-start px-4 py-1.5 border-b border-neutral-800/60 last:border-0 ${STATUS_COLOR[ev.status] ?? 'text-neutral-400'}`}
        >
          <span className="font-mono w-4 flex-shrink-0">{STATUS_ICON[ev.status] ?? '?'}</span>
          <span className="flex-1">{ev.stage}</span>
          {ev.retry_attempt > 0 && (
            <span className="text-yellow-600 flex-shrink-0">retry {ev.retry_attempt}</span>
          )}
          {ev.error_msg && (
            <span className="text-red-400 truncate max-w-[160px]" title={ev.error_msg}>
              {ev.error_msg}
            </span>
          )}
          <span className="text-neutral-600 flex-shrink-0 tabular-nums">
            {new Date(ev.ts * 1000).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </span>
        </div>
      ))}
    </div>
  )
}
