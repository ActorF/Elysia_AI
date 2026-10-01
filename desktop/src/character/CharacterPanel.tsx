/**
 * Present optional character context from Backend and renderer lifecycle facts.
 * This view is read-only and never mutates Chat, Memory, or model state.
 */

import type { BackendSnapshot } from '../../electron/contracts.ts'
import { Icon } from '../design-system/Icon.tsx'
import { CharacterArtwork } from './CharacterArtwork.tsx'
import type { CharacterStateSnapshot } from './character-state.ts'

interface CharacterPanelProps {
  chatTitle: string
  characterState: CharacterStateSnapshot
  modal: boolean
  pending: boolean
  snapshot: BackendSnapshot
  onClose(): void
}

/** Render Elysia's collapsible presence panel for the active conversation. */
export function CharacterPanel({
  chatTitle,
  characterState,
  modal,
  pending,
  snapshot,
  onClose,
}: CharacterPanelProps) {
  return (
    <aside
      className="character-panel"
      data-character-state={characterState.state}
      id="character-panel"
      aria-labelledby="character-panel-title"
      aria-modal={modal ? true : undefined}
      role={modal ? 'dialog' : undefined}
      tabIndex={-1}
    >
      <div className="character-panel-header">
        <div>
          <span className="eyebrow">Elysia</span>
          <h2 id="character-panel-title">Here with you</h2>
        </div>
        <div className="character-panel-actions">
          <span className="soft-status">
            {snapshot.status === 'ready' ? 'Ready' : 'Waiting'}
          </span>
          <button
            type="button"
            className="icon-button character-panel-close"
            data-panel-initial-focus
            aria-label="Close Elysia character panel"
            title="Close panel"
            disabled={pending}
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>
      </div>

      <div className="character-card">
        <CharacterArtwork
          className="character-portrait"
          state={characterState.state}
        />
        <div className="character-caption">
          <strong>{characterState.label}</strong>
          <span>{characterState.description}</span>
        </div>
      </div>

      <div className="context-card">
        <span className="context-label">Current context</span>
        <strong>{chatTitle}</strong>
        <p>{snapshot.modelName ?? 'Waiting for the local model'}</p>
      </div>

      <div className="context-card subtle">
        <span className="context-label">Presence</span>
        <p>
          Collapse this optional panel whenever you want the conversation to
          use the full window width.
        </p>
      </div>

      <div className="context-card subtle character-note">
        <Icon name="info" />
        <p>The character area never changes Chat or Memory data.</p>
      </div>
    </aside>
  )
}
