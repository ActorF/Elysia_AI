import { StrictMode } from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { AppErrorBoundary } from './AppErrorBoundary.tsx'
import './design-system/global.css'
import {
  initializeDocumentTheme,
  ThemeProvider,
} from './theme/ThemeProvider.tsx'

const initialThemeState = initializeDocumentTheme()

function waitForInitialPaint(): Promise<void> {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => { resolve() })
    })
  })
}

async function renderApp(): Promise<void> {
  const root = createRoot(document.getElementById('root')!)
  flushSync(() => {
    root.render(
      <StrictMode>
        <ThemeProvider>
          <AppErrorBoundary>
            <App />
          </AppErrorBoundary>
        </ThemeProvider>
      </StrictMode>,
    )
  })
  const desktopApi = window.elysiaDesktop
  await desktopApi?.setThemePreference(initialThemeState.theme)
  await waitForInitialPaint()
  await desktopApi?.rendererReady()
}

function showFatalRendererError(error: unknown): void {
  console.error('Elysia renderer initialization failed.', error)
  const root = document.getElementById('root')
  if (root !== null) {
    root.className = 'fatal-renderer'
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
