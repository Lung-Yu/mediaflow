import { type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { TaskAccordion } from './TaskAccordion'
import type { StatusOverview } from '@/api/types'

const PIPELINE_STAGES = ['preprocess', 'transcribe', 'diarize', 'summarize']

function StagePips({ current }: { current: string | null }) {
  const curIdx = current ? PIPELINE_STAGES.indexOf(current) : -1
  return (
    <div className="flex gap-1">
      {PIPELINE_STAGES.map((s, i) => (
        <div
          key={s}
          title={s}
          className={`w-2 h-2 rounded-full ${
            s === current ? 'bg-blue-400 animate-pulse' :
            i < curIdx    ? 'bg-neutral-500' :
                            'bg-neutral-800'
          }`}
        />
      ))}
    </div>
  )
}

function Section({ title, count, colorClass, children }: {
  title: string; count: number; colorClass: string; children: ReactNode
}) {
  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm font-semibold text-neutral-300">{title}</span>
        {count > 0 && (
          <span className={`text-xs px-1.5 py-0.5 rounded ${colorClass}`}>{count}</span>
        )}
      </div>
      {children}
    </div>
  )
}

function CancelButton({ stem }: { stem: string }) {
  const qc = useQueryClient()
  const cancel = useMutation({
    mutationFn: () => api.cancelTask(stem),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  return (
    <button
      onClick={() => { if (confirm(`Cancel ${stem}?`)) cancel.mutate() }}
      className="text-xs px-2 py-0.5 bg-red-950 text-red-300 border border-red-800 rounded hover:bg-red-900"
    >
      cancel
    </button>
  )
}

export function JobList({ data }: { data: StatusOverview }) {
  return (
    <div>
      {/* Now Processing */}
      <Section title="Now Processing" count={data.processing.length} colorClass="bg-blue-950 text-blue-300">
        {data.processing.length === 0 ? (
          <p className="text-xs text-neutral-600">No active jobs</p>
        ) : (
          <div className="space-y-2">
            {data.processing.map(t => (
              <div key={t.stem} className="flex items-center gap-3 bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-3">
                <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse flex-shrink-0" />
                <span className="flex-1 text-sm truncate">{t.filename || t.stem}</span>
                <StagePips current={t.current_stage} />
                <span className="text-xs text-neutral-500">{t.current_stage ?? '—'}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* Queue */}
      {data.queue.length > 0 && (
        <Section title="Queue" count={data.queue.length} colorClass="bg-yellow-950 text-yellow-300">
          <div className="space-y-2">
            {data.queue.map(t => (
              <div key={t.stem} className="flex items-center gap-3 bg-neutral-900 border border-neutral-800 rounded-lg px-4 py-3">
                <span className="w-2 h-2 rounded-full bg-yellow-400 flex-shrink-0" />
                <span className="flex-1 text-sm truncate">{t.filename || t.stem}</span>
                <span className="text-xs text-neutral-500">waiting</span>
                <CancelButton stem={t.stem} />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Recent Completions */}
      <Section title="Recent Completions" count={data.recent.length} colorClass="bg-green-950 text-green-300">
        {data.recent.length === 0 ? (
          <p className="text-xs text-neutral-600">No completions yet</p>
        ) : (
          <div className="space-y-2">
            {data.recent.map(t => <TaskAccordion key={t.stem} task={t} />)}
          </div>
        )}
      </Section>

      {/* Failed */}
      {data.failed.length > 0 && (
        <Section title="Failed" count={data.failed.length} colorClass="bg-red-950 text-red-300">
          <div className="space-y-2">
            {data.failed.map(t => (
              <div key={t.stem} className="flex items-center gap-3 bg-neutral-900 border border-red-900 rounded-lg px-4 py-3">
                <span className="w-2 h-2 rounded-full bg-red-400 flex-shrink-0" />
                <span className="flex-1 text-sm truncate">{t.filename || t.stem}</span>
                <span className="text-xs text-red-400 truncate max-w-xs" title={t.error_msg ?? ''}>
                  {t.error_msg || 'unknown error'}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}
