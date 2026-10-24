/**
 * Present the expanded panel's semantic states with the supplied UI illustration
 * sheet. Panel-specific cropping keeps the Chat avatars and Voice Call artwork
 * independent, and static portrait/text fallbacks never interrupt those states.
 */

import { useState, type CSSProperties } from 'react'
import { t, useLocale } from '../i18n/LocaleProvider.tsx'

import type { CharacterEmotion } from './character-emotion.ts'
import { getCharacterPresentation } from './character-presentation.ts'
import type { CharacterState } from './character-state.ts'

interface CharacterPanelArtworkProps {
  emotion: CharacterEmotion
  state: CharacterState
}

interface CharacterPanelIllustrationStyle extends CSSProperties {
  '--character-ui-image-size': string
  '--character-ui-image-x': string
  '--character-ui-image-y': string
}

interface PanelIllustrationCell {
  readonly column: 0 | 1 | 2 | 3
  readonly row: 0 | 1 | 2 | 3
}

// These are reviewed visual poses from the supplied 4x4 UI sheet, rather than
// new backend states: the calm waiting pose reads as listening, the thought
// bubble as thinking, and the speech bubble as speaking. The reading, approval,
// and disconnected illustrations keep their original task-related meaning.
// Emotion remains metadata so a voice preference cannot replace this sheet.
const PANEL_ILLUSTRATION_BY_STATE = {
  idle: { column: 0, row: 0 },
  listening: { column: 2, row: 0 },
  thinking: { column: 2, row: 1 },
  speaking: { column: 0, row: 1 },
  working: { column: 0, row: 2 },
  waiting_approval: { column: 1, row: 3 },
  error: { column: 3, row: 0 },
} as const satisfies Readonly<Record<CharacterState, PanelIllustrationCell>>

// The reviewed 1254px sheet has uneven gutters and row heights. Equal-quarter
// slicing includes the next row's head ornament, especially in the reading
// pose. These source-pixel boundaries exclude adjoining cells without modifying
// the asset. A centered square within each rectangle preserves face proportions
// while removing only a few pixels at the outer shoulder/ornament edges.
const ILLUSTRATION_COLUMNS = [
  { start: 0, end: 308 },
  { start: 315, end: 624 },
  { start: 629, end: 939 },
  { start: 945, end: 1254 },
] as const
const ILLUSTRATION_ROWS = [
  { start: 0, end: 306 },
  { start: 309, end: 622 },
  { start: 626, end: 916 },
  { start: 921, end: 1237 },
] as const

/**
 * Render a fixed reviewed cell for the current state while retaining meaningful
 * state metadata and accessible portrait text if either bundled image fails.
 */
export function CharacterPanelArtwork({
  emotion,
  state,
}: CharacterPanelArtworkProps) {
  useLocale()
  const [illustrationsFailed, setIllustrationsFailed] = useState(false)
  const [portraitFailed, setPortraitFailed] = useState(false)
  const presentation = getCharacterPresentation(state)
  const illustration = PANEL_ILLUSTRATION_BY_STATE[state]
  const assetStage = !illustrationsFailed
    ? 'ui-illustrations'
    : !portraitFailed ? 'portrait' : 'unavailable'
  const column = ILLUSTRATION_COLUMNS[illustration.column]
  const row = ILLUSTRATION_ROWS[illustration.row]
  const cropSize = Math.min(column.end - column.start, row.end - row.start)
  const cropX = column.start + (column.end - column.start - cropSize) / 2
  const cropY = row.start + (row.end - row.start - cropSize) / 2
  const illustrationStyle: CharacterPanelIllustrationStyle = {
    '--character-ui-image-size': `${1254 / cropSize * 100}%`,
    '--character-ui-image-x': `${cropX / -1254 * 100}%`,
    '--character-ui-image-y': `${cropY / -1254 * 100}%`,
  }

  return (
    <div
      className="character-artwork character-panel-artwork"
      data-character-action={presentation.action}
      data-character-asset={assetStage}
      data-character-emotion={emotion}
      data-character-expression={presentation.expression}
      data-character-performance="still"
      data-character-state={state}
      data-ui-illustration-column={illustration.column}
      data-ui-illustration-row={illustration.row}
    >
      {assetStage === 'ui-illustrations' ? (
        <div className="character-panel-illustration-frame">
          <img
            className="character-artwork-image character-panel-illustration-image"
            src="./character/elysia-ui-illustrations.png"
            alt={t("Elysia character portrait")}
            decoding="async"
            style={illustrationStyle}
            onError={() => { setIllustrationsFailed(true) }}
          />
        </div>
      ) : assetStage === 'portrait' ? (
        <img
          className="character-artwork-image character-panel-portrait-fallback"
          src="./character/elysia-portrait.png"
          alt={t("Elysia character portrait")}
          decoding="async"
          onError={() => { setPortraitFailed(true) }}
        />
      ) : (
        <div
          className="character-artwork-fallback"
          role="img"
          aria-label={t("Elysia character portrait unavailable")}
        >
          <span className="character-panel-fallback-initial" aria-hidden="true">E</span>
          <span aria-hidden="true">{t("Artwork unavailable")}</span>
        </div>
      )}
    </div>
  )
}
