import { memo, useRef, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import type { AIState } from '../types'

// ── GLSL shaders ─────────────────────────────────────────────────────────────

const vertexShader = /* glsl */ `
  uniform float uTime;
  uniform float uIntensity;
  varying vec3  vNormal;
  varying float vElevation;

  void main() {
    vNormal = normalize(normalMatrix * normal);

    float e = uIntensity * 0.14 * (
      sin(position.x * 3.1 + uTime * 1.4) *
      sin(position.y * 2.3 + uTime * 0.9) *
      sin(position.z * 4.1 + uTime * 1.1)
    );
    vElevation = e;

    gl_Position = projectionMatrix * modelViewMatrix * vec4(position + normal * e, 1.0);
  }
`

// Opaque fragment shader — no alpha < 1, avoids all transparent-sort flickering
const fragmentShader = /* glsl */ `
  uniform vec3  uColor;
  uniform float uEmissive;
  varying vec3  vNormal;
  varying float vElevation;

  void main() {
    float fresnel = pow(1.0 - abs(dot(vNormal, vec3(0.0, 0.0, 1.0))), 2.2);
    vec3  col     = uColor * (0.35 + fresnel * 0.65 + max(vElevation, 0.0) * 4.0);
    gl_FragColor  = vec4(col * uEmissive, 1.0);
  }
`

// ── Targets per state ────────────────────────────────────────────────────────

type Targets = { r: number; g: number; b: number; intensity: number; emissive: number; scale: number }

function stateTargets(state: AIState, rms: number): Targets {
  switch (state) {
    case 'listening':
      return { r: 0.02, g: 0.72, b: 0.83, intensity: 1.2 + rms * 0.9, emissive: 2.8, scale: 0.94 }
    case 'speaking':
      return { r: 0.09, g: 0.75, b: 0.30, intensity: 1.3 + rms * 1.1, emissive: 3.2, scale: 1.06 }
    default:
      return { r: 0.40, g: 0.12, b: 0.72, intensity: 0.9 + rms * 0.2, emissive: 1.8, scale: 1.00 }
  }
}

// ── Component ────────────────────────────────────────────────────────────────

interface Props { state: AIState; rms: number }

export const AICore = memo(function AICore({ state, rms }: Props) {
  const meshRef  = useRef<THREE.Mesh>(null!)
  const stateRef = useRef(state)
  const rmsRef   = useRef(rms)
  stateRef.current = state
  rmsRef.current   = rms

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms: {
          uTime:      { value: 0 },
          uIntensity: { value: 1.0 },
          uColor:     { value: new THREE.Color(0.4, 0.12, 0.72) },
          uEmissive:  { value: 1.3 },
        },
        // Opaque — no depth-sort flickering
        transparent: false,
        side: THREE.FrontSide,
      }),
    [],
  )

  const targetColor = useMemo(() => new THREE.Color(), [])
  const targetScale = useMemo(() => new THREE.Vector3(), [])

  useFrame((_, delta) => {
    const t   = stateTargets(stateRef.current, rmsRef.current)
    const uni = material.uniforms
    const lf  = 1 - Math.exp(-6 * delta)

    uni.uTime.value      += delta
    uni.uIntensity.value += (t.intensity - uni.uIntensity.value) * lf
    uni.uEmissive.value  += (t.emissive  - uni.uEmissive.value)  * lf
    targetColor.setRGB(t.r, t.g, t.b)
    ;(uni.uColor.value as THREE.Color).lerp(targetColor, lf)

    targetScale.setScalar(t.scale)
    meshRef.current.scale.lerp(targetScale, lf)
  })

  const geometry = useMemo(() => new THREE.IcosahedronGeometry(1.8, 7), [])

  return <mesh ref={meshRef} geometry={geometry} material={material} />
})
