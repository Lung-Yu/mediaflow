import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { lazy, Suspense } from 'react'

const Dashboard   = lazy(() => import('@/pages/Dashboard').then(m => ({ default: m.Dashboard })))
const Transcripts = lazy(() => import('@/pages/Transcripts').then(m => ({ default: m.Transcripts })))
const SrtViewer   = lazy(() => import('@/pages/SrtViewer').then(m => ({ default: m.SrtViewer })))
const Upload      = lazy(() => import('@/pages/Upload').then(m => ({ default: m.Upload })))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10_000, refetchOnWindowFocus: false },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Suspense fallback={<div className="p-8 text-neutral-500">loading…</div>}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/"                     element={<Dashboard />} />
              <Route path="/transcripts"          element={<Transcripts />} />
              <Route path="/transcripts/:stem"    element={<SrtViewer />} />
              <Route path="/upload"               element={<Upload />} />
              <Route path="*"                     element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
