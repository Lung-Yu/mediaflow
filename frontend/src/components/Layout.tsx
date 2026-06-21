import { useState, useEffect } from 'react'
import { Outlet } from 'react-router-dom'

function LiveClock() {
  const [clock, setClock] = useState('')
  useEffect(() => {
    const tick = () => setClock(new Date().toISOString().replace('T', ' ').slice(0, 19))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="text-neutral-500 text-xs tabular-nums">{clock}</span>
}

export function Layout() {
  return (
    <div className="h-screen flex flex-col bg-neutral-950 text-neutral-100 font-mono overflow-hidden">
      <header className="border-b border-neutral-800 px-6 py-3 flex items-center justify-between flex-shrink-0">
        <div className="text-lg font-bold tracking-tight">
          media<span className="text-purple-400">flow</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5 text-neutral-400 text-xs">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            live
          </span>
          <LiveClock />
        </div>
      </header>
      <main className="flex-1 flex overflow-hidden">
        <Outlet />
      </main>
    </div>
  )
}
