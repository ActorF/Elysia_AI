/**
 * Render the shared static Elysia portrait with an accessible failure state.
 * The image is presentation-only and never participates in Chat or Voice state.
 */

import { useState } from 'react'

interface CharacterArtworkProps {
  className?: string
}

/** Display the packaged Elysia portrait, replacing a failed image without exposing a broken control. */
export function CharacterArtwork({ className }: CharacterArtworkProps) {
  const [imageUnavailable, setImageUnavailable] = useState(false)
  const classes = ['character-artwork', className]
    .filter((value): value is string => value !== undefined && value.length > 0)
    .join(' ')

  return (
    <div className={classes}>
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
