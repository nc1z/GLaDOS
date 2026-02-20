import { createRoot } from 'react-dom/client'
import App from './App'

const root = document.getElementById('root')
if (!root) throw new Error('#root not found')

// StrictMode intentionally mounts components twice in development.
// This causes @react-three/postprocessing to register effects twice → flickering.
createRoot(root).render(<App />)
