import { memo, useRef, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import type { AIState } from '../types'

const N = 900

function sphereRandom(rMin: number, rMax: number): [number, number, number] {
  const phi   = Math.acos(2 * Math.random() - 1)
  const theta = 2 * Math.PI * Math.random()
  const r     = rMin + Math.random() * (rMax - rMin)
  return [
    r * Math.sin(phi) * Math.cos(theta),
    r * Math.sin(phi) * Math.sin(theta),
    r * Math.cos(phi),
  ]
}

interface Props { state: AIState }

export const Particles = memo(function Particles({ state }: Props) {
  const pointsRef = useRef<THREE.Points>(null!)

  // Keep latest state accessible inside useFrame without re-registering the callback
  const stateRef = useRef(state)
  stateRef.current = state

  // Build geometry imperatively once — avoids args={[...]} recreation on re-renders
  const { geometry, pos, origins } = useMemo(() => {
    const pos     = new Float32Array(N * 3)
    const origins = new Float32Array(N * 3)
    for (let i = 0; i < N; i++) {
      const [x, y, z] = sphereRandom(2.6, 5.0)
      pos[i * 3] = x;     pos[i * 3 + 1] = y;     pos[i * 3 + 2] = z
      origins[i * 3] = x; origins[i * 3 + 1] = y; origins[i * 3 + 2] = z
    }
    const geometry = new THREE.BufferGeometry()
    // Pass pos by reference — mutations in useFrame are reflected directly
    geometry.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    return { geometry, pos, origins }
  }, [])

  const mat = useMemo(
    () =>
      new THREE.PointsMaterial({
        color:           '#8b5cf6',
        size:            0.055,
        transparent:     true,
        opacity:         0.80,
        sizeAttenuation: true,
        depthWrite:      false,
        blending:        THREE.AdditiveBlending, // bright points add together = glow
      }),
    [],
  )

  const targetColor = useMemo(() => new THREE.Color(), [])

  useFrame((_, delta) => {
    const s    = stateRef.current
    const attr = geometry.attributes['position'] as THREE.BufferAttribute
    const lf   = 1 - Math.exp(-5 * delta)

    for (let i = 0; i < N; i++) {
      const ix = i * 3, iy = i * 3 + 1, iz = i * 3 + 2
      const ox = origins[ix], oy = origins[iy], oz = origins[iz]

      if (s === 'listening') {
        pos[ix] += (0 - pos[ix]) * delta * 0.80
        pos[iy] += (0 - pos[iy]) * delta * 0.80
        pos[iz] += (0 - pos[iz]) * delta * 0.80
        if (pos[ix] ** 2 + pos[iy] ** 2 + pos[iz] ** 2 < 0.8) {
          pos[ix] = ox; pos[iy] = oy; pos[iz] = oz
        }
      } else if (s === 'speaking') {
        const d = Math.sqrt(pos[ix] ** 2 + pos[iy] ** 2 + pos[iz] ** 2)
        if (d > 0.01) {
          const inv = (delta * 2.5) / d
          pos[ix] += pos[ix] * inv
          pos[iy] += pos[iy] * inv
          pos[iz] += pos[iz] * inv
        }
        if (pos[ix] ** 2 + pos[iy] ** 2 + pos[iz] ** 2 > 42) {
          const [x, y, z] = sphereRandom(0.3, 0.6)
          pos[ix] = x; pos[iy] = y; pos[iz] = z
        }
      } else {
        pos[ix] += (ox - pos[ix]) * delta * 0.6
        pos[iy] += (oy - pos[iy]) * delta * 0.6
        pos[iz] += (oz - pos[iz]) * delta * 0.6
      }
    }

    // pos IS attr.array (same Float32Array reference) so just flag dirty
    attr.needsUpdate = true

    // Lerp colour + opacity
    const lf2 = 1 - Math.exp(-4 * delta)
    targetColor.set(
      s === 'listening' ? '#22d3ee' : s === 'speaking' ? '#4ade80' : '#8b5cf6',
    )
    mat.color.lerp(targetColor, lf2)
    mat.opacity += ((s === 'idle' ? 0.50 : 0.92) - mat.opacity) * lf
  })

  return <points ref={pointsRef} geometry={geometry} material={mat} />
})
