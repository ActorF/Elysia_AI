/**
 * Render real Live2D presence with reviewed state and portrait fallbacks.
 * Semantic producers provide only closed state/emotion values; this component
 * alone owns visual selection and optional animated-controller lifecycle.
 */

import {
  useCallback,
  useEffect,
  useState,
  type CSSProperties,
} from 'react'

import { useCharacterPerformance } from './CharacterPerformanceProvider.tsx'
import { Live2DCharacterCanvas } from './Live2DCharacterCanvas.tsx'
import type { CharacterEmotion } from './character-emotion.ts'
import {
  getCharacterEmotionPresentation,
  getCharacterPresentation,
} from './character-presentation.ts'
import type { CharacterState } from './character-state.ts'
import type { Live2DFraming } from './live2d-runtime.ts'

interface CharacterArtworkProps {
  className?: string
  emotion?: CharacterEmotion
  live2DFraming?: Exclude<Live2DFraming, 'full-body'>
  state: CharacterState
}

type CharacterAssetStage =
  | 'live2d'
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
 * Animated mode first attempts the local Live2D model, retaining reviewed
 * static artwork while it loads or when it fails. Still/Reduced Motion never
 * mounts the controller. Independent atlas and portrait fallback boundaries
 * keep Voice and Chat controls usable even when visual assets are unavailable.
 */
export function CharacterArtwork({
  className,
  emotion = 'neutral',
  live2DFraming = 'half-body',
  state,
}: CharacterArtworkProps) {
  const [speechAtlasFailed, setSpeechAtlasFailed] = useState(false)
  const [expressionAtlasFailed, setExpressionAtlasFailed] = useState(false)
  const [stateAtlasFailed, setStateAtlasFailed] = useState(false)
  const [portraitFailed, setPortraitFailed] = useState(false)
  const [live2DStatus, setLive2DStatus] = useState<
    'loading' | 'ready' | 'failed'
  >('loading')
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
  let fallbackAssetStage: Exclude<CharacterAssetStage, 'live2d'>
  if (wantsSpeechAtlas && !speechAtlasFailed) {
    fallbackAssetStage = 'speech-atlas'
  } else if (wantsExpressionAtlas && !expressionAtlasFailed) {
    fallbackAssetStage = 'expression-atlas'
  } else if (!stateAtlasFailed) {
    fallbackAssetStage = 'atlas'
  } else if (!portraitFailed) {
    fallbackAssetStage = 'portrait'
  } else {
    fallbackAssetStage = 'unavailable'
  }
  const live2DEnabled = resolvedMode === 'animated' && live2DStatus !== 'failed'
  const assetStage: CharacterAssetStage = live2DEnabled
    && live2DStatus === 'ready'
    ? 'live2d'
    : fallbackAssetStage
  const mouthCapable = assetStage === 'live2d'
    || assetStage === 'speech-atlas'
  const handleLive2DStatus = useCallback((
    status: 'loading' | 'ready' | 'failed',
  ): void => {
    setLive2DStatus(status)
  }, [])

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
        || (assetStage === 'live2d' && emotion !== 'neutral')
        ? emotionPresentation.expression
        : statePresentation.expression}
      data-character-mouth-capable={mouthCapable ? 'true' : undefined}
      data-character-performance={resolvedMode}
      data-character-state={state}
    >
      {live2DEnabled ? (
        <Live2DCharacterCanvas
          emotion={emotion}
          framing={live2DFraming}
          state={state}
          onStatusChange={handleLive2DStatus}
        />
      ) : null}
      {assetStage === 'live2d' ? null : assetStage === 'unavailable' ? (
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
