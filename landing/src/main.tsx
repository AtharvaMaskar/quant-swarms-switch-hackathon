import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { SWITCH_URL } from './data.ts'
import './styles.css'

// /open is a shortcut straight to the Switch research floor.
if (location.pathname.replace(/\/$/, '') === '/open') location.replace(SWITCH_URL)
document.documentElement.classList.add('js')
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
