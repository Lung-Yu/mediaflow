import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { StatusOverview } from '@/api/types'
import { api } from '@/api/client'

export function useSSE() {
  const queryClient = useQueryClient()

  useEffect(() => {
    const es = new EventSource(api.sseUrl())

    es.addEventListener('status', (e: MessageEvent) => {
      const data = JSON.parse(e.data) as StatusOverview
      const norm = (tasks: StatusOverview[keyof StatusOverview]) =>
        tasks.map(t => ({ ...t, stem: t.stem ?? t.id }))
      queryClient.setQueryData<StatusOverview>(['status'], {
        processing: norm(data.processing),
        queue: norm(data.queue),
        recent: norm(data.recent),
        failed: norm(data.failed),
      })
    })

    return () => es.close()
  }, [queryClient])
}
