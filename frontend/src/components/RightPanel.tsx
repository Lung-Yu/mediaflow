import { useState, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { useDebounce } from '@/hooks/useDebounce'
import { AudioPlayer } from '@/components/AudioPlayer'
import { SrtSegmentList } from '@/components/SrtSegmentList'
import { SrtEditor } from '@/components/SrtEditor'
import { SummarySection } from '@/components/SummarySection'
import { KeywordList } from '@/components/KeywordList'
import { JobAuditLog } from '@/components/JobAuditLog'

type Tab = 'summary' | 'keywords' | 'edit' | 'log'

interface Props {
  stem: string | null
}

export function RightPanel({ stem }: Props) {
  const [q, setQ] = useState('')
  const [tab, setTab] = useState<Tab>('summary')
  const [savedOnce, setSavedOnce] = useState(false)
  const [currentTime, setCurrentTime] = useState(-1)
  const [bottomH, setBottomH] = useState<number | null>(null) // null = 50/50 via flex
  const audioRef = useRef<HTMLAudioElement>(null)
  const resizeAreaRef = useRef<HTMLDivElement>(null)
  const debouncedQ = useDebounce(q, 300)

  useEffect(() => { setSavedOnce(false) }, [stem])

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

  const onDragBottom = (e: React.MouseEvent) => {
    e.preventDefault()
    const y0 = e.clientY
    const h0 = bottomH ?? (resizeAreaRef.current ? resizeAreaRef.current.clientHeight / 2 : 300)
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev: MouseEvent) => {
      const avail = resizeAreaRef.current?.clientHeight ?? 600
      setBottomH(Math.max(80, Math.min(h0 - (ev.clientY - y0), avail - 80)))
    }
    const onUp = () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

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

      {/* Resizable split area */}
      <div ref={resizeAreaRef} className="flex-1 flex flex-col overflow-hidden min-h-0">

        {/* Transcript */}
        <div className="flex-1 overflow-y-auto min-h-0">
          <SrtSegmentList
            segments={segments}
            currentTime={currentTime}
            onSeek={hasAudio ? (t: number) => { if (audioRef.current) audioRef.current.currentTime = t } : undefined}
          />
        </div>

        {/* Drag handle */}
        <div
          className="h-1 flex-shrink-0 bg-neutral-800 cursor-row-resize hover:bg-purple-600/50 active:bg-purple-500/70 transition-colors"
          onMouseDown={onDragBottom}
        />

        {/* Bottom panel — tabs */}
        <div
          className="flex-shrink-0 flex flex-col"
          style={bottomH !== null ? { height: bottomH } : { flex: 1 }}
        >
          {/* Tab bar */}
          <div className="flex flex-shrink-0 border-b border-neutral-800">
            {([['summary', '摘要'], ['keywords', '關鍵字'], ['edit', '校正逐字稿'], ['log', '紀錄']] as [Tab, string][]).map(([key, label]) => (
              <button key={key} onClick={() => setTab(key)}
                className={`px-4 py-2 text-xs font-medium transition-colors border-b-2 -mb-px ${tab === key ? 'text-purple-300 border-purple-500' : 'text-neutral-500 border-transparent hover:text-neutral-300'}`}
              >{label}</button>
            ))}
          </div>

          {/* Tab content */}
          <div className={`flex-1 overflow-y-auto min-h-0${tab !== 'edit' ? ' px-4 py-3' : ''}`}>
            {tab === 'summary' && <SummarySection stem={stem} />}
            {tab === 'keywords' && <KeywordList />}
            {tab === 'edit' && (
            <SrtEditor
              stem={stem}
              onSeek={hasAudio ? (t) => { if (audioRef.current) audioRef.current.currentTime = t } : undefined}
              savedOnce={savedOnce}
              setSavedOnce={setSavedOnce}
            />
          )}
          {tab === 'log' && <JobAuditLog stem={stem} />}
          </div>
        </div>
      </div>
    </div>
  )
}
