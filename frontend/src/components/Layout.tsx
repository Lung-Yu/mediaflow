import { useState, useEffect } from 'react'
import { Outlet, NavLink } from 'react-router-dom'

function LiveClock() {
  const [clock, setClock] = useState('')
  useEffect(() => {
    const tick = () => {
      const d = new Date()
      setClock(d.toISOString().replace('T', ' ').slice(0, 19))
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  return <span className="text-neutral-500 text-xs tabular-nums">{clock}</span>
}

const navCls = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'text-purple-400' : 'text-neutral-400 hover:text-neutral-200 transition-colors'

export function Layout() {
  return (
    <div className="min-h-screen flex flex-col bg-neutral-950 text-neutral-100 font-mono">
      <header className="border-b border-neutral-800 px-6 py-3 flex items-center justify-between flex-shrink-0">
        <div className="text-lg font-bold tracking-tight">
          media<span className="text-purple-400">flow</span>
        </div>
        <div className="flex items-center gap-6 text-sm">
          <nav className="flex gap-5">
            <NavLink to="/" end className={navCls}>dashboard</NavLink>
            <NavLink to="/transcripts" className={navCls}>transcripts</NavLink>
            <NavLink to="/upload" className={navCls}>upload</NavLink>
          </nav>
          <span className="flex items-center gap-1.5 text-neutral-400 text-xs">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            live
          </span>
          <LiveClock />
        </div>
      </header>

      <main className="flex-1 max-w-5xl w-full mx-auto px-6 py-6">
        <Outlet />
      </main>

      <footer className="border-t border-neutral-800 px-6 py-3 text-xs text-neutral-600 flex justify-between flex-shrink-0">
        <span>mediaflow pipeline monitor</span>
        <span>SSE live</span>
      </footer>
    </div>
  )
}
