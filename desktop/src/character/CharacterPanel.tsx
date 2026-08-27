/**
 * Present optional character context derived from the current Backend snapshot.
 * This view is read-only and never mutates Chat, Memory, or model state.
 */

import type { BackendSnapshot } from '../../electron/contracts.ts'
import { Icon } from '../design-system/Icon.tsx'

interface CharacterPanelProps {
  chatTitle: string
  snapshot: BackendSnapshot
}

/** Render Elysia's collapsible presence panel for the active conversation. */
export function CharacterPanel({
  chatTitle,
  snapshot,
}: CharacterPanelProps) {
  return (
    <aside
      className="character-panel"
      id="character-panel"
      aria-label="Elysia character panel"
    >
      <div className="character-panel-header">
        <div>
          <span className="eyebrow">Elysia</span>
          <h2>Here with you</h2>
        </div>
        <span className="soft-status">
          {snapshot.status === 'ready' ? 'Ready' : 'Waiting'}
        </span>
      </div>

      <div className="character-card">
        <div
          className="character-portrait"
          aria-label="Character artwork placeholder"
          role="img"
        >
          <div className="portrait-hair" />
          <div className="portrait-face" />
          <div className="portrait-shoulders" />
          <span>Character artwork</span>
        </div>
        <div className="character-caption">
          <strong>Pink fairy at your side</strong>
          <span>Visual assets arrive in a later character feature.</span>
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
