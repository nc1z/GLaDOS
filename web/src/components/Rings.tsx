import { memo, useRef, useEffect, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import type { AIState } from '../types'

interface RingConfig {
  radius: number
  tube:   number
  rotX:   number
  rotZ:   number
  speed:  number
  colorIdle:      string
  colorListening: string
  colorSpeaking:  string
}

const RING_CONFIGS: RingConfig[] = [
  { radius: 2.6, tube: 0.018, rotX:  Math.PI / 2,  rotZ: 0,           speed:  0.30, colorIdle: '#6d28d9', colorListening: '#06b6d4', colorSpeaking: '#22c55e' },
  { radius: 3.2, tube: 0.015, rotX:  Math.PI / 4,  rotZ: Math.PI / 6, speed: -0.20, colorIdle: '#5b21b6', colorListening: '#0891b2', colorSpeaking: '#16a34a' },
  { radius: 3.9, tube: 0.012, rotX: -Math.PI / 5,  rotZ: Math.PI / 3, speed:  0.14, colorIdle: '#4c1d95', colorListening: '#0e7490', colorSpeaking: '#15803d' },
]

interface SingleRingProps {
  config:      RingConfig
  state:       AIState
  index:       number
}

// memo — only re-renders when state changes (not on rms/other parent updates)
const SingleRing = memo(function SingleRing({ config, state, index }: SingleRingProps) {
  const groupRef = useRef<THREE.Group>(null!)

  // Keep latest state accessible inside useFrame without stale closures
  const stateRef = useRef(state)
  stateRef.current = state

  // Build geometry once in useMemo — avoids args={[...]} triggering recreation
  const geometry = useMemo(
    () => new THREE.TorusGeometry(config.radius, config.tube, 16, 180),
    // config is a stable object reference from module-level RING_CONFIGS
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  const mat = useMemo(
    () =>
      new THREE.MeshBasicMaterial({
        color:      config.colorIdle,
        transparent: true,
        opacity:     0.9,
        depthWrite:  false,
        blending:    THREE.AdditiveBlending, // additive = natural glow, no post-processing needed
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  const targetColor = useMemo(() => new THREE.Color(), [])
  const targetScale = useMemo(() => new THREE.Vector3(), [])
  const phaseOffset = index * 0.42

  useEffect(() => {
    groupRef.current.rotation.x = config.rotX
    groupRef.current.rotation.z = config.rotZ
  }, [config.rotX, config.rotZ])

  useFrame((state3f, delta) => {
    const s  = stateRef.current
    const t  = state3f.clock.elapsedTime + phaseOffset
    const lf = 1 - Math.exp(-5 * delta)

    groupRef.current.rotation.y += delta * config.speed
    groupRef.current.rotation.x += delta * config.speed * 0.25

    const scaleMult =
      s === 'listening' ? 0.70 + Math.sin(t * 2.5) * 0.08
      : s === 'speaking'  ? 1.10 + Math.sin(t * 3.2) * 0.12
      : 1.00 + Math.sin(t * 0.8) * 0.02

    targetScale.setScalar(scaleMult)
    groupRef.current.scale.lerp(targetScale, lf)

    targetColor.set(
      s === 'listening' ? config.colorListening
      : s === 'speaking'  ? config.colorSpeaking
      : config.colorIdle,
    )
    mat.color.lerp(targetColor, lf)
    mat.opacity += ((s === 'idle' ? 0.35 : 0.92) - mat.opacity) * lf
  })

  return (
    <group ref={groupRef}>
      <mesh geometry={geometry} material={mat} />
    </group>
  )
})

interface Props { state: AIState }

export const Rings = memo(function Rings({ state }: Props) {
  return (
    <>
      {RING_CONFIGS.map((cfg, i) => (
        <SingleRing key={i} config={cfg} state={state} index={i} />
      ))}
    </>
  )
})
