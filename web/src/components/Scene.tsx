import { Suspense, memo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Stars, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { AICore } from './AICore'
import { Rings } from './Rings'
import { Particles } from './Particles'
import { useAIState } from '../hooks/useAIState'
import { useAudio } from '../hooks/useAudio'
import type { AIState } from '../types'

// ── Reactive point light ──────────────────────────────────────────────────────

const ReactiveLight = memo(function ReactiveLight({ state }: { state: AIState }) {
  const ref        = useRef<THREE.PointLight>(null!)
  const stateRef   = useRef(state)
  const targetColor = useRef(new THREE.Color())
  stateRef.current = state

  useFrame((_, delta) => {
    if (!ref.current) return
    const s  = stateRef.current
    const lf = 1 - Math.exp(-4 * delta)
    targetColor.current.set(
      s === 'listening' ? '#0891b2' : s === 'speaking' ? '#16a34a' : '#6d28d9',
    )
    ref.current.color.lerp(targetColor.current, lf)
    ref.current.intensity += ((s === 'idle' ? 1.5 : 3.5) - ref.current.intensity) * lf
  })

  return <pointLight ref={ref} position={[0, 0, 4]} intensity={1.5} distance={14} />
})

// ── Scene content ─────────────────────────────────────────────────────────────

const SceneContent = memo(function SceneContent({ state, rms }: { state: AIState; rms: number }) {
  return (
    <>
      <ambientLight intensity={0.05} />
      <ReactiveLight state={state} />

      <AICore     state={state} rms={rms} />
      <Rings      state={state} />
      <Particles  state={state} />

      <Stars radius={60} depth={20} count={1500} factor={2.5} saturation={0.3} />

      <OrbitControls
        enableZoom={false}
        enablePan={false}
        autoRotate
        autoRotateSpeed={0.3}
        maxPolarAngle={Math.PI * 0.65}
        minPolarAngle={Math.PI * 0.35}
      />
    </>
  )
})

// ── Status overlay ────────────────────────────────────────────────────────────

const STATE_COLORS: Record<AIState, string> = {
  idle:      '#a78bfa',
  listening: '#22d3ee',
  speaking:  '#4ade80',
}

const STATE_LABELS: Record<AIState, string> = {
  idle:      '· · ·',
  listening: '⬤  LISTENING',
  speaking:  '⬤  SPEAKING',
}

function StatusOverlay({ state }: { state: AIState }) {
  return (
    <div
      style={{
        position:       'fixed',
        inset:          0,
        pointerEvents:  'none',
        display:        'flex',
        flexDirection:  'column',
        alignItems:     'center',
        justifyContent: 'flex-end',
        paddingBottom:  '48px',
        fontFamily:     "'Courier New', Courier, monospace",
      }}
    >
      <div
        style={{
          color:         STATE_COLORS[state],
          fontSize:      '13px',
          letterSpacing: '0.35em',
          transition:    'color 0.6s',
          textShadow:    `0 0 20px ${STATE_COLORS[state]}cc`,
        }}
      >
        {STATE_LABELS[state]}
      </div>
      <div
        style={{
          marginTop:     '16px',
          color:         '#ffffff22',
          fontSize:      '11px',
          letterSpacing: '0.5em',
        }}
      >
        J A R V I S
      </div>
    </div>
  )
}

// ── Audio enable / status button ──────────────────────────────────────────────

function AudioButton({
  enabled,
  lastChunk,
  onEnable,
}: {
  enabled: boolean
  lastChunk: number
  onEnable: () => void
}) {
  if (!enabled) {
    return (
      <button
        onClick={onEnable}
        style={{
          position:       'fixed',
          top:            '24px',
          right:          '24px',
          zIndex:         10,
          background:     'rgba(255,255,255,0.06)',
          border:         '1px solid rgba(255,255,255,0.18)',
          borderRadius:   '8px',
          color:          '#a78bfa',
          fontFamily:     "'Courier New', Courier, monospace",
          fontSize:       '12px',
          letterSpacing:  '0.2em',
          padding:        '10px 18px',
          cursor:         'pointer',
          backdropFilter: 'blur(6px)',
        }}
      >
        ▶ ENABLE AUDIO
      </button>
    )
  }

  // Show a brief flash on each received chunk; otherwise show a muted "audio on" badge
  const age = Date.now() - lastChunk
  const fresh = lastChunk > 0 && age < 1200
  return (
    <div
      style={{
        position:      'fixed',
        top:           '24px',
        right:         '24px',
        zIndex:        10,
        fontFamily:    "'Courier New', Courier, monospace",
        fontSize:      '11px',
        letterSpacing: '0.2em',
        color:          fresh ? '#4ade80' : '#ffffff22',
        transition:    'color 0.4s',
        padding:       '10px 18px',
      }}
    >
      {fresh ? '♪ AUDIO' : '· AUDIO ON'}
    </div>
  )
}

// ── Root ──────────────────────────────────────────────────────────────────────

export function Scene() {
  const { state, rms }              = useAIState()
  const { enabled, enable, lastChunk } = useAudio()

  return (
    <div style={{ width: '100vw', height: '100vh', background: '#000005' }}>
      <Canvas
        camera={{ position: [0, 0, 9], fov: 42 }}
        gl={{ antialias: true, alpha: false }}
        dpr={[1, 2]}
      >
        <Suspense fallback={null}>
          <SceneContent state={state} rms={rms} />
        </Suspense>
      </Canvas>

      <StatusOverlay state={state} />
      <AudioButton enabled={enabled} lastChunk={lastChunk} onEnable={enable} />
    </div>
  )
}
