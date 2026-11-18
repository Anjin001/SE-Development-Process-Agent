import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { WorkbenchEntry } from './WorkbenchEntry'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <WorkbenchEntry />
  </StrictMode>,
)
