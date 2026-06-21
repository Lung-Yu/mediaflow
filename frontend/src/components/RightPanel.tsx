import { useState, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useDebounce } from '@/hooks/useDebounce'
import { AudioPlayer } from '@/components/AudioPlayer'
import { SrtSegmentList } from '@/components/SrtSegmentList'
import { SrtEditor } from '@/components/SrtEditor'

interface Props {
  stem: string | null
}

export function RightPanel({ stem }: Props) {
  const [q, setQ] = useState('')
  const [currentTime, setCurrentTime] = useState(-1)
  const audioRef = useRef<HTMLAudioElement>(null)
  const debouncedQ = useDebounce(q, 300)

  const { data: speakerData } = useQuery({
    queryKey: ['speaker-data', stem],
    queryFn: () => api.getSpeakerData(stem!),
    enabled: !!stem,
    staleTime: 60_000,
  })

  const { data: segments = [] } = useQuery({
    queryKey: ['segments', stem, debouncedQ],
    queryFn: () => api.getSegments(stem!, debouncedQ || undefined),
    enabled: !!stem,
    staleTime: 30_000,
  })

  if (!stem) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-neutral-600">
        ← 從左側選擇一個逐字稿開始
      </div>
    )
  }

  const hasAudio = speakerData?.has_audio ?? false

  return (
    <div className="flex-1 flex flex-col overflow-hidden border-l border-neutral-800">
      {hasAudio && (
        <AudioPlayer
          audioRef={audioRef}
          src={api.audioUrl(stem)}
          onTimeUpdate={setCurrentTime}
        />
      )}

      {/* Title bar */}
      <div className="flex-shrink-0 flex items-center gap-3 px-4 py-2.5 border-b border-neutral-800">
        <span className="text-sm font-medium text-neutral-300 flex-1 truncate">{stem}</span>
        <input
          type="search"
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="搜尋段落…"
          className="w-44 text-xs bg-neutral-900 border border-neutral-700 rounded px-2 py-1 text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-purple-600"
        />
        <a
          href={`/api/files/${stem}/srt`}
          download={`${stem}.srt`}
          className="text-xs px-2.5 py-1 border border-neutral-700 text-neutral-400 rounded hover:border-purple-600 hover:text-purple-400 transition-colors"
        >
          下載 SRT
        </a>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto">
        <SrtEditor stem={stem} />
        <SrtSegmentList
          segments={segments}
          currentTime={currentTime}
          onSeek={hasAudio ? (t: number) => { if (audioRef.current) audioRef.current.currentTime = t } : undefined}
        />
        <div />
      </div>
    </div>
  )
}
