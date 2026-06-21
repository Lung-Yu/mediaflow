import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function SummarySection({ stem }: { stem: string }) {
  const { data: summary, isFetching } = useQuery({
    queryKey: ['summary', stem],
    queryFn: () => api.getSummary(stem),
    staleTime: Infinity,
  })

  if (isFetching) return <p className="text-xs text-neutral-600">載入中…</p>
  if (summary == null) return <p className="text-xs text-neutral-600">（無摘要）</p>
  return <p className="text-xs text-neutral-300 leading-relaxed whitespace-pre-wrap">{summary}</p>
}
