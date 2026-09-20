import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { App } from './App'
import { PageShell } from './components/PageShell'
import { EventDetail } from './pages/EventDetail'
import './styles.css'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000, networkMode: 'always' } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <PageShell>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/events/:eventId" element={<EventDetail />} />
          <Route path="*" element={
              <main id="main" tabIndex={-1} className="flex flex-1 flex-col items-start py-20 focus:outline-none">
                <title>Page not found · EventScout</title>
                <h1 className="font-display text-4xl font-semibold">Page not found.</h1>
                <Link className="mt-6 text-scout underline" to="/">Back to EventScout</Link>
              </main>
          } />
        </Routes>
        </PageShell>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
