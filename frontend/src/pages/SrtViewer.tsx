import { useState, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { AudioPlayer } from '@/components/AudioPlayer'
import { SrtSegmentList } from '@/components/SrtSegmentList'
import { TimelinePanel } from '@/components/TimelinePanel'
import { SpeakerPanel } from '@/components/SpeakerPanel'
import { SrtEditor } from '@/components/SrtEditor'

export function SrtViewer() {
  const { stem = '' } = useParams()
  const [q, setQ] = useState('')
  const [currentTime, setCurrentTime] = useState(-1)
  const audioRef = useRef<HTMLAudioElement>(null)

  const { data: speakerData } = useQuery({
    queryKey: ['speaker-data', stem],
    queryFn: () => api.getSpeakerData(stem),
    staleTime: 60_000,
  })
  const { data: segments = [] } = useQuery({
    queryKey: ['segments', stem, q],
    queryFn: () => api.getSegments(stem, q || undefined),
    staleTime: 30_000,
  })
  const { data: timeline = null } = useQuery({
    queryKey: ['timeline', stem],
    queryFn: () => api.getTimeline(stem),
    staleTime: 60_000,
  })

  const hasAudio = speakerData?.has_audio ?? false

  const seekTo = (t: number) => {
    if (audioRef.current) audioRef.current.currentTime = t
  }

  return (
    <div>
      {hasAudio && (
        <AudioPlayer
          ref={audioRef}
          src={api.audioUrl(stem)}
          onTimeUpdate={setCurrentTime}
        />
      )}

      <div className="flex items-center gap-4 mb-4 mt-4">
        <h1 className="text-sm font-semibold text-neutral-300 flex-1 truncate">{stem}</h1>
        <input
          type="search"
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="搜尋逐字稿…"
          className="w-56 text-sm bg-neutral-900 border border-neutral-700 rounded px-3 py-1.5 text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-purple-600"
        />
        <span className="text-xs text-neutral-600">{segments.length} 段</span>
      </div>

      {speakerData && speakerData.speakers.length > 0 && (
        <SpeakerPanel stem={stem} speakerData={speakerData} />
      )}

      <TimelinePanel timeline={timeline} />

      <SrtEditor stem={stem} />

      <SrtSegmentList
        segments={segments}
        currentTime={currentTime}
        onSeek={hasAudio ? seekTo : undefined}
      />

      <div className="mt-6 pt-4 border-t border-neutral-800 flex justify-between text-xs text-neutral-500">
        <Link to="/transcripts" className="hover:text-purple-400">← 返回逐字稿列表</Link>
        <span>{segments.length} 段落</span>
      </div>
    </div>
  )
}
