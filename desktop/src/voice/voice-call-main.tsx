/** Mount only the privacy-limited Voice Call renderer and its shared design reset. */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { AppErrorBoundary } from '../AppErrorBoundary.tsx'
import { initializeDocumentLocale, LocaleProvider } from '../i18n/LocaleProvider.tsx'
import '../design-system/global.css'
import { VoiceCallWindow } from './VoiceCallWindow.tsx'
import './VoiceCallWindow.css'

initializeDocumentLocale()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LocaleProvider>
      <AppErrorBoundary>
        <VoiceCallWindow />
      </AppErrorBoundary>
    </LocaleProvider>
  </StrictMode>,
)
