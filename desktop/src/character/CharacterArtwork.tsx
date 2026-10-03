/**
 * Render the application's deliberately static character state artwork.
 *
 * Dynamic motion is delegated to an optional external desktop-pet program.
 * Keeping this surface image-only gives Chat and Voice a predictable half-body
 * portrait and keeps external program files out of the app UI.
 */

import { useState, type CSSProperties } from 'react'

import type { CharacterEmotion } from './character-emotion.ts'
import {
  getCharacterEmotionPresentation,
  getCharacterPresentation,
} from './character-presentation.ts'
import type { CharacterState } from './character-state.ts'

interface CharacterArtworkProps {
  className?: string
  emotion?: CharacterEmotion
  state: CharacterState
  variant?: 'state' | 'avatar'
}

type CharacterAssetStage =
  | 'expression-atlas'
  | 'atlas'
  | 'portrait'
  | 'unavailable'

interface CharacterStateAtlasStyle extends CSSProperties {
  '--character-atlas-column': number
  '--character-atlas-row': number
}

interface CharacterExpressionAtlasStyle extends CSSProperties {
  '--character-expression-column': number
  '--character-expression-row': number
}

/**
 * Display one bounded character visual without making artwork authoritative.
 *
 * State and emotion changes may select a different reviewed image, while the
 * avatar variant deliberately prefers the square expression atlas for circular
 * crops. No canvas, sprite animation, or lip-synchronization is mounted here.
 * Independent atlas and portrait fallbacks keep Voice and Chat usable when an
 * asset fails.
 */
export function CharacterArtwork({
  className,
  emotion = 'neutral',
  state,
  variant = 'state',
}: CharacterArtworkProps) {
  const [expressionAtlasFailed, setExpressionAtlasFailed] = useState(false)
  const [stateAtlasFailed, setStateAtlasFailed] = useState(false)
  const [portraitFailed, setPortraitFailed] = useState(false)
  const statePresentation = getCharacterPresentation(state)
  const emotionPresentation = getCharacterEmotionPresentation(emotion)
  const classes = ['character-artwork', className]
    .filter((value): value is string => value !== undefined && value.length > 0)
    .join(' ')
  const stateAtlasStyle: CharacterStateAtlasStyle = {
    '--character-atlas-column': statePresentation.atlasColumn,
    '--character-atlas-row': statePresentation.atlasRow,
  }
  const expressionAtlasStyle: CharacterExpressionAtlasStyle = {
    '--character-expression-column': emotionPresentation.atlasColumn,
    '--character-expression-row': emotionPresentation.atlasRow,
  }
  const wantsExpressionAtlas = variant === 'avatar' || (
    state === 'speaking'
    || (state === 'idle' && emotion !== 'neutral')
  )
  let assetStage: CharacterAssetStage
  if (wantsExpressionAtlas && !expressionAtlasFailed) {
    assetStage = 'expression-atlas'
  } else if (!stateAtlasFailed) {
    assetStage = 'atlas'
  } else if (!portraitFailed) {
    assetStage = 'portrait'
  } else {
    assetStage = 'unavailable'
  }

  return (
    <div
      className={classes}
      data-character-action={statePresentation.action}
      data-character-asset={assetStage}
      data-character-emotion={emotion}
      data-character-expression={assetStage === 'expression-atlas'
        ? emotionPresentation.expression
        : statePresentation.expression}
      data-character-performance="still"
      data-character-state={state}
    >
      {assetStage === 'unavailable' ? (
        <div
          className="character-artwork-fallback"
          role="img"
          aria-label="Elysia character portrait unavailable"
        >
          <span className="character-artwork-fallback-mark" aria-hidden="true">
            ✦
          </span>
          <span aria-hidden="true">Artwork unavailable</span>
        </div>
      ) : assetStage === 'expression-atlas' ? (
        <div className="character-artwork-expression-frame">
          <img
            className="character-artwork-image character-artwork-expression-atlas"
            src="./character/elysia-expression-atlas.png"
            alt={`Elysia ${emotion} expression`}
            decoding="async"
            style={expressionAtlasStyle}
            onError={() => { setExpressionAtlasFailed(true) }}
          />
        </div>
      ) : assetStage === 'atlas' ? (
        <div className="character-artwork-frame">
          <img
            className="character-artwork-image character-artwork-atlas"
            src="./character/elysia-state-atlas.png"
            alt="Elysia character portrait"
            decoding="async"
            style={stateAtlasStyle}
            onError={() => { setStateAtlasFailed(true) }}
          />
        </div>
      ) : (
        <img
          className="character-artwork-image character-artwork-static"
          src="./character/elysia-portrait.png"
          alt="Elysia character portrait"
          decoding="async"
          onError={() => { setPortraitFailed(true) }}
        />
      )}
    </div>
  )
}
