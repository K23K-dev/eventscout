const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()
const apiBaseUrl = (configuredBaseUrl || 'http://127.0.0.1:8000').replace(/\/+$/, '')

export async function checkHealth(signal: AbortSignal): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/health`, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(5_000)]),
  })
  if (!response.ok) throw new Error('The service is unavailable.')

  const payload = await response.json()
  if (payload?.status !== 'ok') {
    throw new Error('The service returned an unexpected response.')
  }
}
