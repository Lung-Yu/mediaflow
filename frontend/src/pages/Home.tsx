import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { LeftPanel } from '@/components/LeftPanel'
import { RightPanel } from '@/components/RightPanel'

export function Home() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [stem, setStem] = useState<string | null>(searchParams.get('stem'))
  const [leftWidth, setLeftWidth] = useState(280)

  useEffect(() => {
    const urlStem = searchParams.get('stem')
    if (urlStem !== stem) setStem(urlStem)
  }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectStem = (s: string) => {
    setStem(s)
    setSearchParams({ stem: s }, { replace: true })
  }

  const onDragLeft = (e: React.MouseEvent) => {
    e.preventDefault()
    const x0 = e.clientX, w0 = leftWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev: MouseEvent) => setLeftWidth(Math.max(180, Math.min(w0 + ev.clientX - x0, 560)))
    const onUp = () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div className="flex w-full overflow-hidden h-full">
      <div className="flex flex-col overflow-hidden flex-shrink-0 border-r border-neutral-800" style={{ width: leftWidth }}>
        <LeftPanel selectedStem={stem} onSelect={selectStem} />
      </div>
      <div
        className="w-1 flex-shrink-0 cursor-col-resize hover:bg-purple-600/50 active:bg-purple-500 transition-colors -ml-px z-10"
        onMouseDown={onDragLeft}
      />
      <RightPanel stem={stem} />
    </div>
  )
}
