import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { App } from './App'
import './styles.css'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="*" element={
            <main className="page-shell">
              <h1>Page not found.</h1>
              <Link className="text-link" to="/">Back to EventScout</Link>
            </main>
          } />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
