import { useRef, useState, useCallback } from 'react'

function fmt(s: number) {
  if (!isFinite(s)) return '0:00'
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

interface Props {
  src: string
  onTimeUpdate?: (t: number) => void
  audioRef?: React.RefObject<HTMLAudioElement>
}

export function AudioPlayer({ src, onTimeUpdate, audioRef: externalRef }: Props) {
  const internalRef = useRef<HTMLAudioElement>(null)
  const audioRef = externalRef ?? internalRef

  const [playing, setPlaying] = useState(false)
  const [pct, setPct] = useState(0)
  const [label, setLabel] = useState('0:00 / 0:00')

  const toggle = () => {
    if (!audioRef.current) return
    audioRef.current.paused ? audioRef.current.play() : audioRef.current.pause()
  }

  const handleTime = () => {
    const el = audioRef.current
    if (!el) return
    const p = el.duration ? (el.currentTime / el.duration) * 100 : 0
    setPct(p)
    setLabel(`${fmt(el.currentTime)} / ${fmt(el.duration)}`)
    onTimeUpdate?.(el.currentTime)
  }

  const seek = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const el = audioRef.current
    if (!el?.duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    el.currentTime = ((e.clientX - rect.left) / rect.width) * el.duration
  }, [audioRef])

  return (
    <div className="sticky top-0 z-10 flex items-center gap-3 bg-neutral-900 border-b border-neutral-800 px-4 py-2">
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={handleTime}
      />
      <button
        onClick={toggle}
        className="w-8 h-8 flex items-center justify-center text-neutral-200 hover:text-purple-400"
      >
        {playing ? '⏸' : '▶'}
      </button>
      <div
        className="flex-1 h-1.5 bg-neutral-700 rounded-full cursor-pointer relative"
        onClick={seek}
      >
        <div className="absolute left-0 top-0 h-full bg-purple-400 rounded-full" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-neutral-500 tabular-nums w-28 text-right">{label}</span>
    </div>
  )
}
