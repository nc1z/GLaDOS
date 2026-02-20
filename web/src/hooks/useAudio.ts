import { useCallback, useEffect, useRef, useState } from 'react'

const API_BASE: string = import.meta.env['VITE_API_URL'] ?? 'http://localhost:7860'

export function useAudio() {
  const [enabled, setEnabled]     = useState(false)
  const [lastChunk, setLastChunk] = useState<number>(0)

  const audioCtxRef = useRef<AudioContext | null>(null)
  const nextTimeRef = useRef(0)
  const enabledRef  = useRef(false)
  const esRef       = useRef<EventSource | null>(null)
  const retryRef    = useRef<ReturnType<typeof setTimeout> | null>(null)

  /** Create (or return) AudioContext and ensure it's running. */
  const getCtx = useCallback((): AudioContext => {
    if (!audioCtxRef.current) {
      audioCtxRef.current = new AudioContext()
      console.log('[audio] AudioContext created, state:', audioCtxRef.current.state)
    }
    if (audioCtxRef.current.state === 'suspended') {
      void audioCtxRef.current.resume().then(() =>
        console.log('[audio] AudioContext resumed, state:', audioCtxRef.current?.state),
      )
    }
    return audioCtxRef.current
  }, [])

  /** Unlock Web Audio — must be called from a user-gesture handler. */
  const enable = useCallback(() => {
    if (enabledRef.current) return
    enabledRef.current = true
    setEnabled(true)
    const ctx = getCtx()
    console.log('[audio] Enabled by user gesture. AudioContext state:', ctx.state)
  }, [getCtx])

  // Try auto-enable immediately on mount — works if the page has already had
  // any prior user gesture (e.g. browser was already open/interacted with).
  useEffect(() => {
    try {
      const ctx = new AudioContext()
      audioCtxRef.current = ctx
      console.log('[audio] Auto-created AudioContext, state:', ctx.state)
      if (ctx.state === 'running') {
        enabledRef.current = true
        setEnabled(true)
        console.log('[audio] Auto-enabled (context already running)')
      }
    } catch {
      // will need button click
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Listen for any click on the document to unlock audio.
  useEffect(() => {
    function onGesture() {
      if (!enabledRef.current) {
        enable()
      } else if (audioCtxRef.current?.state === 'suspended') {
        void audioCtxRef.current.resume()
      }
    }
    document.addEventListener('click', onGesture)
    document.addEventListener('keydown', onGesture)
    return () => {
      document.removeEventListener('click', onGesture)
      document.removeEventListener('keydown', onGesture)
    }
  }, [enable])

  useEffect(() => {
    function connect() {
      console.log('[audio] Connecting SSE to', `${API_BASE}/audio/stream`)
      const es = new EventSource(`${API_BASE}/audio/stream`)
      esRef.current = es

      es.onopen = () => console.log('[audio] SSE open')

      es.onmessage = async (event: MessageEvent<string>) => {
        if (!event.data || event.data.startsWith(':')) return
        console.log('[audio] chunk received, size:', event.data.length, '| enabled:', enabledRef.current)

        if (!enabledRef.current) {
          console.log('[audio] queued but audio not yet unlocked — click anywhere to enable')
          return
        }

        try {
          const parsed = JSON.parse(event.data) as { wav?: string; sampleRate?: number }
          const { wav, sampleRate } = parsed
          if (!wav) { console.warn('[audio] missing wav field'); return }

          console.log('[audio] decoding WAV, sampleRate:', sampleRate, 'b64 len:', wav.length)
          const ctx = getCtx()
          console.log('[audio] ctx state:', ctx.state)

          const binary = atob(wav)
          const bytes  = new Uint8Array(binary.length)
          for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)

          const buf     = await ctx.decodeAudioData(bytes.buffer.slice(0))
          const startAt = Math.max(ctx.currentTime + 0.05, nextTimeRef.current)
          const source  = ctx.createBufferSource()
          source.buffer = buf
          source.connect(ctx.destination)
          source.start(startAt)
          nextTimeRef.current = startAt + buf.duration
          setLastChunk(Date.now())
          console.log('[audio] ✓ playing', buf.duration.toFixed(2), 's at t=', startAt.toFixed(3))
        } catch (err) {
          console.error('[audio] error:', err)
        }
      }

      es.onerror = (e) => {
        console.warn('[audio] SSE error — retry in 2s', e)
        es.close()
        esRef.current = null
        retryRef.current = setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      esRef.current?.close()
      if (retryRef.current) clearTimeout(retryRef.current)
    }
  }, [getCtx])

  return { enabled, enable, lastChunk }
}
