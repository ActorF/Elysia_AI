/**
 * Present the non-recording one-to-one call preview and its local controls.
 * Actual voice capture remains disabled until the dedicated voice feature.
 */

import { useEffect, useRef } from 'react'

import { Icon } from '../design-system/Icon.tsx'

interface CallPreviewProps {
  captionsEnabled: boolean
  modelName?: string
  onCaptionsChange(): void
  onClose(): void
}

/** Render the full-window call preview and return focus through onClose. */
export function CallPreview({
  captionsEnabled,
  modelName,
  onCaptionsChange,
  onClose,
}: CallPreviewProps) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    closeButtonRef.current?.focus()
  }, [])

  return (
    <main className="call-page" aria-label="Voice call preview">
      <header className="call-header">
        <div>
          <span className="eyebrow">One-to-one voice</span>
          <h1>Elysia</h1>
        </div>
        <span className="call-model" title={modelName ?? 'Local model'}>
          {modelName ?? 'Local model'}
        </span>
      </header>

      <section className="call-stage" aria-label="Call preview stage">
        <div className="call-aura" aria-hidden="true" />
        <div
          className="character-portrait call-portrait"
          aria-label="Character artwork placeholder"
          role="img"
        >
          <div className="portrait-hair" />
          <div className="portrait-face" />
          <div className="portrait-shoulders" />
          <span>Character artwork</span>
        </div>
        <div className="call-state" role="status">
          <span className="call-state-dot" />
          Preview
        </div>
        {captionsEnabled && (
          <p className="call-caption">
            Continuous voice conversation will connect to this page in the
            dedicated voice work.
          </p>
        )}
      </section>

      <footer className="call-controls">
        <button
          type="button"
          className={'call-control' + (captionsEnabled ? ' active' : '')}
          onClick={onCaptionsChange}
          aria-pressed={captionsEnabled}
        >
          <Icon name="captions" />
          <span>Captions</span>
        </button>
        <button
          type="button"
          className="call-control muted"
          disabled
          title="Voice capture is added in the dedicated voice work"
        >
          <Icon name="microphone" />
          <span>Microphone</span>
        </button>
        <button
          ref={closeButtonRef}
          type="button"
          className="call-control hangup"
          onClick={onClose}
        >
          <Icon name="hangup" />
          <span>End preview</span>
        </button>
      </footer>
    </main>
  )
}
