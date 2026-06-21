import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { StatusBar } from '@/components/StatusBar'
import { JobList } from '@/components/JobList'
import { StatsPanel } from '@/components/StatsPanel'
import { useSSE } from '@/hooks/useSSE'
import type { StatusOverview } from '@/api/types'

const EMPTY: StatusOverview = { processing: [], queue: [], recent: [], failed: [] }

export function Dashboard() {
  useSSE()

  const { data = EMPTY, isError } = useQuery({
    queryKey: ['status'],
    queryFn: api.getStatus,
    refetchInterval: 30_000,
  })

  if (isError) {
    return <p className="text-red-400 text-sm">API unreachable — check that the api container is running.</p>
  }

  return (
    <div>
      <StatsPanel />
      <StatusBar data={data} />
      <JobList data={data} />
    </div>
  )
}
