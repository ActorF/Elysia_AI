import { StrictMode } from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

async function renderApp(): Promise<void> {
  const root = createRoot(document.getElementById('root')!)
  flushSync(() => {
    root.render(
      <StrictMode>
        <App />
      </StrictMode>,
    )
  })
  await window.elysiaDesktop?.rendererReady()
}

function showFatalRendererError(error: unknown): void {
  console.error('Elysia renderer initialization failed.', error)
  const root = document.getElementById('root')
  if (root !== null) {
    const heading = document.createElement('h1')
    heading.textContent = 'Elysia could not initialize.'
    const detail = document.createElement('p')
    detail.textContent = 'Close the application and try again.'
    root.replaceChildren(heading, detail)
  }

  void window.elysiaDesktop?.rendererReady().catch(() => {
    // Electron also has a bounded native visibility fallback.
  })
}

void renderApp().catch(showFatalRendererError)
