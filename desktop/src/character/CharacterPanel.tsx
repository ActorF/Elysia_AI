/**
 * Present optional character context from Backend and renderer lifecycle facts.
 * This view is read-only and never mutates Chat, Memory, or model state.
 */

import { t, useLocale } from '../i18n/LocaleProvider.tsx'

import type { BackendSnapshot } from '../../electron/contracts.ts'
import { Icon } from '../design-system/Icon.tsx'
import { CharacterPanelArtwork } from './CharacterPanelArtwork.tsx'
import type { CharacterEmotion } from './character-emotion.ts'
import type { CharacterStateSnapshot } from './character-state.ts'
import './CharacterPanel.css'

interface CharacterPanelProps {
  chatTitle: string
  characterState: CharacterStateSnapshot
  emotion: CharacterEmotion
  modal: boolean
  pending: boolean
  snapshot: BackendSnapshot
  onClose(): void
}

/** Render Elysia's collapsible presence panel for the active conversation. */
export function CharacterPanel({
  chatTitle,
  characterState,
  emotion,
  modal,
  pending,
  snapshot,
  onClose,
}: CharacterPanelProps) {
  useLocale()
  return (
    <aside
      className="character-panel character-panel-compact"
      data-character-state={characterState.state}
      id="character-panel"
      aria-labelledby="character-panel-title"
      aria-modal={modal ? true : undefined}
      role={modal ? 'dialog' : undefined}
      tabIndex={-1}
    >
      <div className="character-panel-header">
        <h2 id="character-panel-title">{t("Elysia")}</h2>
        <div className="character-panel-actions">
          <button
            type="button"
            className="icon-button character-panel-close"
            data-panel-initial-focus
            aria-label={t("Close Elysia character panel")}
            title={t("Close panel")}
            disabled={pending}
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>
      </div>

      <div className="character-panel-body">
        <div className="character-presence-card">
          <CharacterPanelArtwork emotion={emotion} state={characterState.state} />
          <div
            className="character-caption"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <strong className="character-presence-label" title={t(characterState.description)}>
              <span className="status-dot" aria-hidden="true" />
              {t(characterState.label)}
            </strong>
          </div>
        </div>
      </div>

      <footer className="character-panel-footer">
        <div className="character-context-row">
          <span className="context-label">{t("Chat")}</span>
          <strong title={chatTitle}>{chatTitle}</strong>
        </div>
        <div className="character-context-row">
          <span className="context-label">{t("Model")}</span>
          <span title={snapshot.modelName ?? undefined}>
            {snapshot.modelName ?? t("Waiting for the local model")}
          </span>
        </div>
      </footer>
    </aside>
  )
}
