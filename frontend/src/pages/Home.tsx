import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSSE } from '@/hooks/useSSE'
import { LeftPanel } from '@/components/LeftPanel'

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
      <LeftPanel selectedStem={stem} onSelect={selectStem} />
      <div className="flex-1 flex items-center justify-center text-xs text-neutral-600">
        right panel (coming soon)
      </div>
    </div>
  )
}
