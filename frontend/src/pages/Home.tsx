import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSSE } from '@/hooks/useSSE'

export function Home() {
  useSSE()
  const [searchParams, setSearchParams] = useSearchParams()
  const [stem, setStem] = useState<string | null>(searchParams.get('stem'))

  useEffect(() => {
    const urlStem = searchParams.get('stem')
    if (urlStem !== stem) setStem(urlStem)
  }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectStem = (s: string) => {
    setStem(s)
    setSearchParams({ stem: s }, { replace: true })
  }

  return (
    <div className="flex w-full overflow-hidden">
      <div className="w-80 flex-shrink-0 border-r border-neutral-800 p-4 text-xs text-neutral-600">
        left panel (coming soon) — selected: {stem ?? 'none'}
      </div>
      <div className="flex-1 p-4 text-xs text-neutral-600">
        right panel (coming soon)
        <button className="ml-4 text-purple-400" onClick={() => selectStem('test')}>test select</button>
      </div>
    </div>
  )
}
