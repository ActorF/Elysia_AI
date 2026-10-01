/**
 * Render reviewed state, expression, and speech atlases with safe fallbacks.
 * Semantic producers provide only closed state/emotion values; this component
 * alone owns files, atlas coordinates, and the optional animated close-up.
 */

import { useEffect, useState, type CSSProperties } from 'react'

import { useCharacterPerformance } from './CharacterPerformanceProvider.tsx'
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
}

type CharacterAssetStage =
  | 'speech-atlas'
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

const CHARACTER_VISUAL_REFRESH_EVENT = 'elysia:character-visual-refresh'

/**
 * Display one bounded character visual without making artwork authoritative.
 *
 * Actual speech uses the reviewed four-frame mouth band only in Animated mode.
 * Still/Reduced Motion shows the selected static expression. A failed speech
 * or expression atlas falls back independently through the state atlas,
 * portrait, and accessible text so Voice and Chat controls remain usable.
 */
export function CharacterArtwork({
  className,
  emotion = 'neutral',
  state,
}: CharacterArtworkProps) {
  const [speechAtlasFailed, setSpeechAtlasFailed] = useState(false)
  const [expressionAtlasFailed, setExpressionAtlasFailed] = useState(false)
  const [stateAtlasFailed, setStateAtlasFailed] = useState(false)
  const [portraitFailed, setPortraitFailed] = useState(false)
  const { resolvedMode } = useCharacterPerformance()
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
  const wantsSpeechAtlas = state === 'speaking' && resolvedMode === 'animated'
  const wantsExpressionAtlas = (
    state === 'speaking'
    || (state === 'idle' && emotion !== 'neutral')
  )
  let assetStage: CharacterAssetStage
  if (wantsSpeechAtlas && !speechAtlasFailed) {
    assetStage = 'speech-atlas'
  } else if (wantsExpressionAtlas && !expressionAtlasFailed) {
    assetStage = 'expression-atlas'
  } else if (!stateAtlasFailed) {
    assetStage = 'atlas'
  } else if (!portraitFailed) {
    assetStage = 'portrait'
  } else {
    assetStage = 'unavailable'
  }

  useEffect(() => {
    // Preload treats this event only as a request to re-check the DOM. It does
    // not trust Renderer data or use the event for audio/control decisions.
    window.dispatchEvent(new Event(CHARACTER_VISUAL_REFRESH_EVENT))
  }, [assetStage, resolvedMode, state])

  return (
    <div
      className={classes}
      data-character-action={statePresentation.action}
      data-character-asset={assetStage}
      data-character-emotion={emotion}
      data-character-expression={assetStage === 'expression-atlas'
        ? emotionPresentation.expression
        : statePresentation.expression}
      data-character-performance={resolvedMode}
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
      ) : assetStage === 'speech-atlas' ? (
        <div className="character-artwork-speech-frame">
          <img
            className="character-artwork-image character-artwork-speech-atlas"
            src="./character/elysia-speech-atlas.png"
            alt="Elysia speaking"
            decoding="async"
            onError={() => { setSpeechAtlasFailed(true) }}
          />
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
