import { useQuery } from '@tanstack/react-query'
import { API_BASE, rawFetch } from './client'

export function artifactUrl(id: string): string {
  return `${API_BASE}/artifacts/${encodeURIComponent(id)}`
}

/** Lazy-load a text artifact (diffs, terminal output); large artifacts are only fetched when shown. */
export function useArtifactText(id: string | null | undefined, enabled = true, maxBytes = 512 * 1024) {
  return useQuery({
    queryKey: ['artifact-text', id],
    enabled: !!id && enabled,
    staleTime: Infinity,
    queryFn: async () => {
      const res = await rawFetch(`/artifacts/${encodeURIComponent(id ?? '')}`, { headers: { Accept: 'text/plain, */*' } })
      const text = await res.text()
      return text.length > maxBytes ? `${text.slice(0, maxBytes)}\n… (truncated, download the artifact for the rest)` : text
    },
  })
}

/** Download through fetch (keeps the CSRF/cookie rules) and hand the blob to the browser. */
export async function downloadArtifact(id: string, name: string): Promise<void> {
  const res = await rawFetch(`/artifacts/${encodeURIComponent(id)}`)
  const blob = await res.blob()
  saveBlob(blob, name)
}

export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => {
    URL.revokeObjectURL(url)
  }, 10_000)
}
