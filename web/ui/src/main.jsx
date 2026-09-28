import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import '@fontsource-variable/bricolage-grotesque'
import AuthGate from './AuthGate'
import './styles/tokens.css'
import './styles/base.css'
import './styles/shell.css'
import './styles/components.css'
import './styles/lab.css'
import './styles/motion.css'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthGate />
  </StrictMode>,
)
