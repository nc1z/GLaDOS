export type AIState = 'idle' | 'listening' | 'speaking'

export interface AIStatePayload {
  state: AIState
  rms: number
}
