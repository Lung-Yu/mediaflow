import type { Segment } from '@/api/types'

interface Props {
  segments: Segment[]
  currentTime?: number
  onSeek?: (t: number) => void
}

export function SrtSegmentList({ segments, currentTime = -1, onSeek }: Props) {
  if (segments.length === 0) {
    return <p className="text-xs text-neutral-600 py-4">無段落資料</p>
  }

  return (
    <div className="space-y-0">
      {segments.map((seg, i) => {
        const nextStart = segments[i + 1]?.start_seconds ?? Infinity
        const isActive = currentTime >= seg.start_seconds && currentTime < nextStart
        return (
          <div
            key={seg.index}
            data-start={seg.start_seconds}
            onClick={() => onSeek?.(seg.start_seconds)}
            className={`flex gap-3 px-4 py-2.5 cursor-pointer transition-colors border-l-2 ${
              isActive
                ? 'bg-purple-950/40 border-purple-500'
                : 'border-transparent hover:bg-neutral-900'
            }`}
          >
            <span className="text-xs text-neutral-600 tabular-nums w-16 flex-shrink-0 pt-0.5">
              {seg.start.slice(0, 8)}
            </span>
            <span
              className="text-sm text-neutral-200 leading-relaxed flex-1"
              dangerouslySetInnerHTML={{ __html: seg.text }}
            />
          </div>
        )
      })}
    </div>
  )
}
