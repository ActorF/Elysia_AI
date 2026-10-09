/**
 * Collect the bounded source and whole-song key choices for one Song Cover.
 *
 * Native file paths remain owned by Electron Main. This renderer dialog sends
 * reviewed enum-like options plus an optional bounded title/artist identity
 * after explicit user confirmation and discloses the LRCLIB metadata boundary.
 */

import { useId, useState } from 'react'

import type {
  ChooseSongCoverRequest,
  SongCoverEngine,
  SongCoverKeyShiftSemitones,
  SongCoverSourceMode,
} from '../../electron/song-cover-contracts.ts'
import { ChatActionDialog } from '../shell/ChatActionDialog.tsx'

/** Inputs and completion actions for the compact Song Cover setup dialog. */
export interface SongCoverSetupDialogProps {
  /** Whether the native modal surface is currently visible. */
  open: boolean
  /** Close the dialog without opening any native file picker. */
  onCancel(): void
  /** Continue to native source selection with one exact reviewed request. */
  onConfirm(request: ChooseSongCoverRequest): void
}

const DEFAULT_SOURCE_MODE: SongCoverSourceMode = 'song'
const DEFAULT_ENGINE: SongCoverEngine = 'lyrics-svs'
const DEFAULT_KEY_SHIFT: SongCoverKeyShiftSemitones = 0

/** Render keyboard-contained Song Cover choices before native file selection. */
export function SongCoverSetupDialog({
  open,
  onCancel,
  onConfirm,
}: SongCoverSetupDialogProps) {
  const [sourceMode, setSourceMode] = useState<SongCoverSourceMode>(
    DEFAULT_SOURCE_MODE,
  )
  const [engine, setEngine] = useState<SongCoverEngine>(DEFAULT_ENGINE)
  const [keyShiftSemitones, setKeyShiftSemitones]
    = useState<SongCoverKeyShiftSemitones>(DEFAULT_KEY_SHIFT)
  const [songTitle, setSongTitle] = useState('')
  const [songArtist, setSongArtist] = useState('')
  const [metadataError, setMetadataError] = useState<string | null>(null)
  const pitchDescriptionId = useId()
  const metadataDescriptionId = useId()

  function resetChoices(): void {
    setEngine(DEFAULT_ENGINE)
    setSourceMode(DEFAULT_SOURCE_MODE)
    setKeyShiftSemitones(DEFAULT_KEY_SHIFT)
    setSongTitle('')
    setSongArtist('')
    setMetadataError(null)
  }

  function cancel(): void {
    resetChoices()
    onCancel()
  }

  function confirm(): void {
    const title = songTitle.trim()
    const artist = songArtist.trim()
    if (
      engine === 'lyrics-svs'
      && ((title.length === 0) !== (artist.length === 0))
    ) {
      setMetadataError(
        'Enter both song title and artist, or leave both fields blank.',
      )
      return
    }
    const request: ChooseSongCoverRequest = {
      engine,
      sourceMode,
      keyShiftSemitones,
      lyricsMetadataOverride: (
        engine === 'lyrics-svs' && title.length > 0
          ? { title, artist }
          : null
      ),
    }
    resetChoices()
    onConfirm(request)
  }

  return (
    <ChatActionDialog
      confirmLabel="Choose audio"
      description={(
        <p>
          Create a high-clarity cover from a complete song or prepared stems.
        </p>
      )}
      error={metadataError}
      open={open}
      pending={false}
      title="Create Song Cover"
      onCancel={cancel}
      onConfirm={confirm}
    >
      <div className="song-cover-setup-options">
        <fieldset className="song-cover-setup-group">
          <legend>Singing method</legend>
          <div className="song-cover-choice-list">
            <label className={
              'song-cover-choice' + (engine === 'lyrics-svs' ? ' selected' : '')
            }>
              <input
                type="radio"
                name="song-cover-engine"
                value="lyrics-svs"
                checked={engine === 'lyrics-svs'}
                onChange={() => {
                  setEngine('lyrics-svs')
                  setMetadataError(null)
                }}
              />
              <span>
                <strong>Lyrics-driven singing</strong>
                <small>
                  Finds synchronized lyrics online and sings them with the
                  local SoulX runtime.
                </small>
              </span>
            </label>
            <label className={
              'song-cover-choice' + (engine === 'legacy-svc' ? ' selected' : '')
            }>
              <input
                type="radio"
                name="song-cover-engine"
                value="legacy-svc"
                checked={engine === 'legacy-svc'}
                onChange={() => {
                  setEngine('legacy-svc')
                  setMetadataError(null)
                }}
              />
              <span>
                <strong>Legacy voice conversion</strong>
                <small>
                  Explicit fallback. Copies the source vocal pronunciation and
                  does not use online lyrics.
                </small>
              </span>
            </label>
          </div>
        </fieldset>

        {engine === 'lyrics-svs' && (
          <fieldset
            className="song-cover-setup-group"
            aria-describedby={metadataDescriptionId}
          >
            <legend>Online lyrics identity (optional)</legend>
            <p id={metadataDescriptionId}>
              Mandarin songs with synchronized Chinese lyrics only. Leave both
              fields blank to use audio tags or a clear Artist - Title filename.
              ScreenRecording, vocals, and other generic names need both fields.
              {' '}LRCLIB receives the title, artist, and rounded duration to
              find lyrics; your audio is never uploaded.
            </p>
            <div className="song-cover-metadata-fields">
              <label>
                <span>Song title</span>
                <input
                  type="text"
                  value={songTitle}
                  maxLength={256}
                  autoComplete="off"
                  placeholder="Example: Song title"
                  onChange={(event) => {
                    setSongTitle(event.currentTarget.value)
                    setMetadataError(null)
                  }}
                />
              </label>
              <label>
                <span>Artist</span>
                <input
                  type="text"
                  value={songArtist}
                  maxLength={256}
                  autoComplete="off"
                  placeholder="Example: Artist"
                  onChange={(event) => {
                    setSongArtist(event.currentTarget.value)
                    setMetadataError(null)
                  }}
                />
              </label>
            </div>
          </fieldset>
        )}

        <fieldset className="song-cover-setup-group">
          <legend>Audio source</legend>
          <div className="song-cover-choice-list">
            <label className={
              'song-cover-choice' + (sourceMode === 'song' ? ' selected' : '')
            }>
              <input
                type="radio"
                name="song-cover-source"
                value="song"
                checked={sourceMode === 'song'}
                onChange={() => { setSourceMode('song') }}
              />
              <span>
                <strong>Complete song</strong>
                <small>Elysia separates the vocal from one audio file.</small>
              </span>
            </label>
            <label className={
              'song-cover-choice' + (sourceMode === 'stems' ? ' selected' : '')
            }>
              <input
                type="radio"
                name="song-cover-source"
                value="stems"
                checked={sourceMode === 'stems'}
                onChange={() => { setSourceMode('stems') }}
              />
              <span>
                <strong>Vocals + accompaniment</strong>
                <small>Choose isolated vocals first, then accompaniment.</small>
              </span>
            </label>
          </div>
        </fieldset>

        <fieldset
          className="song-cover-setup-group"
          aria-describedby={pitchDescriptionId}
        >
          <legend>Song key</legend>
          <p id={pitchDescriptionId}>
            Original key follows the source melody most closely. Raise only
            for hoarse low notes, or lower only for strained high notes;
            either change intentionally moves every sung pitch.
          </p>
          <div className="song-cover-key-choices">
            {([
              [0, 'Original key'],
              [-1, 'Lower 1'],
              [-2, 'Lower 2'],
              [1, 'Raise 1'],
              [2, 'Raise 2'],
            ] as const).map(([shift, label]) => (
              <label
                className={
                  'song-cover-key-choice'
                  + (keyShiftSemitones === shift ? ' selected' : '')
                }
                key={shift}
              >
                <input
                  type="radio"
                  name="song-cover-key"
                  value={shift}
                  checked={keyShiftSemitones === shift}
                  onChange={() => { setKeyShiftSemitones(shift) }}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>
      </div>
    </ChatActionDialog>
  )
}
