import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { App } from './App'
import { PageShell } from './components/PageShell'
import './styles.css'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="*" element={
            <PageShell>
              <main className="flex flex-col">
                <h1 className="font-display text-display">Page not found.</h1>
                <Link className="mt-6 text-scout underline" to="/">Back to EventScout</Link>
              </main>
            </PageShell>
          } />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
