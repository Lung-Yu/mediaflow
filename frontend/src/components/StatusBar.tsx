import type { StatusOverview } from '@/api/types'

const CELLS = [
  { key: 'processing' as const, label: 'Processing', color: 'text-blue-400',   border: 'border-blue-800' },
  { key: 'queue'      as const, label: 'Queued',     color: 'text-yellow-400', border: 'border-yellow-800' },
  { key: 'recent'     as const, label: 'Completed',  color: 'text-green-400',  border: 'border-green-800' },
  { key: 'failed'     as const, label: 'Failed',     color: 'text-red-400',    border: 'border-red-800' },
]

export function StatusBar({ data }: { data: StatusOverview }) {
  return (
    <div className="grid grid-cols-4 gap-3 mb-6">
      {CELLS.map(({ key, label, color, border }) => (
        <div key={key} className={`bg-neutral-900 border ${border} rounded-lg p-4 text-center`}>
          <div className={`text-3xl font-bold tabular-nums ${color}`}>{data[key].length}</div>
          <div className="text-xs text-neutral-500 mt-1 uppercase tracking-widest">{label}</div>
        </div>
      ))}
    </div>
  )
}
