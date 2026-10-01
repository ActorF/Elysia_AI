/**
 * Render one reviewed Character State atlas cell with bounded motion policy.
 * State producers never select files; a closed local registry owns the visual
 * mapping, and failures degrade through the prior portrait to accessible text.
 */

import { useState, type CSSProperties } from 'react'

import { useCharacterPerformance } from './CharacterPerformanceProvider.tsx'
import { getCharacterPresentation } from './character-presentation.ts'
import type { CharacterState } from './character-state.ts'

interface CharacterArtworkProps {
  className?: string
  state: CharacterState
}

type CharacterAssetStage = 'atlas' | 'portrait' | 'unavailable'

interface CharacterAtlasStyle extends CSSProperties {
  '--character-atlas-column': number
  '--character-atlas-row': number
}

/** Display the reviewed state cell and preserve controls through both fallbacks. */
export function CharacterArtwork({ className, state }: CharacterArtworkProps) {
  const [assetStage, setAssetStage] = useState<CharacterAssetStage>('atlas')
  const { resolvedMode } = useCharacterPerformance()
  const presentation = getCharacterPresentation(state)
  const classes = ['character-artwork', className]
    .filter((value): value is string => value !== undefined && value.length > 0)
    .join(' ')
  const atlasStyle: CharacterAtlasStyle = {
    '--character-atlas-column': presentation.atlasColumn,
    '--character-atlas-row': presentation.atlasRow,
  }

  return (
    <div
      className={classes}
      data-character-action={presentation.action}
      data-character-asset={assetStage}
      data-character-expression={presentation.expression}
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
      ) : assetStage === 'atlas' ? (
        <div className="character-artwork-frame">
          <img
            className="character-artwork-image character-artwork-atlas"
            src="./character/elysia-state-atlas.png"
            alt="Elysia character portrait"
            decoding="async"
            style={atlasStyle}
            onError={() => { setAssetStage('portrait') }}
          />
        </div>
      ) : (
        <img
          className="character-artwork-image character-artwork-static"
          src="./character/elysia-portrait.png"
          alt="Elysia character portrait"
          decoding="async"
          onError={() => { setAssetStage('unavailable') }}
        />
      )}
    </div>
  )
}
