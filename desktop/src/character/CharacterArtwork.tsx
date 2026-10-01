/**
 * Render the shared static Elysia portrait with an accessible failure state.
 * Semantic state is exposed as metadata and never selects or switches the
 * fixed image or any motion.
 */

import { useState } from 'react'

import type { CharacterState } from './character-state.ts'

interface CharacterArtworkProps {
  className?: string
  state: CharacterState
}

/** Display the packaged portrait and preserve semantic state when image loading fails. */
export function CharacterArtwork({ className, state }: CharacterArtworkProps) {
  const [imageUnavailable, setImageUnavailable] = useState(false)
  const classes = ['character-artwork', className]
    .filter((value): value is string => value !== undefined && value.length > 0)
    .join(' ')

  return (
    <div className={classes} data-character-state={state}>
      {imageUnavailable ? (
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
      ) : (
        <img
          className="character-artwork-image"
          src="./character/elysia-portrait.png"
          alt="Elysia character portrait"
          decoding="async"
          onError={() => { setImageUnavailable(true) }}
        />
      )}
    </div>
  )
}
