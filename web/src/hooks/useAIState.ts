import { useEffect, useRef, useState } from 'react'
import type { AIStatePayload } from '../types'

const API_BASE: string = import.meta.env['VITE_API_URL'] ?? 'http://localhost:7860'

export function useAIState(): AIStatePayload {
  const [payload, setPayload] = useState<AIStatePayload>({ state: 'idle', rms: 0 })
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    function connect() {
      const es = new EventSource(`${API_BASE}/state/stream`)
      esRef.current = es

      es.onmessage = (event: MessageEvent<string>) => {
        try {
          const data = JSON.parse(event.data) as AIStatePayload
          setPayload({ state: data.state ?? 'idle', rms: data.rms ?? 0 })
        } catch {
          // ignore malformed messages
        }
      }

      es.onerror = () => {
        es.close()
        esRef.current = null
        // retry after 2 s — backend may be starting up
        retryRef.current = setTimeout(connect, 2000)
      }
    }

    connect()

    return () => {
      esRef.current?.close()
      if (retryRef.current) clearTimeout(retryRef.current)
    }
  }, [])

  return payload
}
