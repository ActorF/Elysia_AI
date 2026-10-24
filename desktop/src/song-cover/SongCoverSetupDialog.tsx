/**
 * Collect singing method, source, and whole-song key in the themed music studio.
 *
 * Native file paths remain owned by Electron Main. This renderer dialog sends
 * reviewed enum-like options plus an optional bounded title/artist identity
 * after explicit user confirmation and discloses the LRCLIB metadata boundary.
 */

import { t, useTranslation } from '../i18n/LocaleProvider.tsx'
import { useId, useState } from 'react'

import {
  SONG_COVER_KEY_SHIFTS,
  type ChooseSongCoverRequest,
  type SongCoverEngine,
  type SongCoverKeyShiftSemitones,
  type SongCoverSingingReference,
  type SongCoverSourceMode,
} from '../../electron/song-cover-contracts.ts'
import { Icon } from '../design-system/Icon.tsx'
import { HelpHint } from '../design-system/HelpHint.tsx'
import { ChatActionDialog } from '../shell/ChatActionDialog.tsx'

import './SongCover.css'

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
const DEFAULT_ENGINE: SongCoverEngine = 'source-svc'
const DEFAULT_KEY_SHIFT: SongCoverKeyShiftSemitones = 0

/** Render keyboard-contained Song Cover choices before native file selection. */
export function SongCoverSetupDialog({
  open,
  onCancel,
  onConfirm,
}: SongCoverSetupDialogProps) {
  useTranslation()
  const [sourceMode, setSourceMode] = useState<SongCoverSourceMode>(
    DEFAULT_SOURCE_MODE,
  )
  const [engine, setEngine] = useState<SongCoverEngine>(DEFAULT_ENGINE)
  const [singingReference, setSingingReference] = useState<SongCoverSingingReference>('fairy')
  const [vocalArrangement, setVocalArrangement] = useState<'entire' | 'lead'>('entire')
  const [keyShiftSemitones, setKeyShiftSemitones]
    = useState<SongCoverKeyShiftSemitones>(DEFAULT_KEY_SHIFT)
  const [shiftAccompaniment, setShiftAccompaniment] = useState(true)
  const [songTitle, setSongTitle] = useState('')
  const [songArtist, setSongArtist] = useState('')
  const [metadataError, setMetadataError] = useState<string | null>(null)
  const pitchDescriptionId = useId()
  const metadataDescriptionId = useId()
  const optionDescriptionId = useId()
  const leadOnly = engine !== 'lyrics-svs' && vocalArrangement === 'lead'

  function changeKey(value: number): void {
    const allowedShift = SONG_COVER_KEY_SHIFTS.find((shift) => shift === value)
    if (allowedShift !== undefined) setKeyShiftSemitones(allowedShift)
  }

  function resetChoices(): void {
    setEngine(DEFAULT_ENGINE)
    setSingingReference('fairy')
    setVocalArrangement('entire')
    setSourceMode(DEFAULT_SOURCE_MODE)
    setKeyShiftSemitones(DEFAULT_KEY_SHIFT)
    setShiftAccompaniment(true)
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
        t("Enter both song title and artist, or leave both fields blank."),
      )
      return
    }
    const request: ChooseSongCoverRequest = {
      engine,
      singingReference: engine === 'source-svc' ? singingReference : null,
      // Lead-only is a prepared-stems workflow, not another separation model.
      // Main receives the same reviewed two-file contract; the backing file
      // must already contain the voices the user wants to keep unchanged.
      sourceMode: leadOnly ? 'stems' : sourceMode,
      keyShiftSemitones,
      shiftAccompaniment,
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
      confirmLabel={t("Choose audio")}
      description={null}
      error={metadataError === null ? null : t(metadataError)}
      open={open}
      pending={false}
      title={t("Create Song Cover")}
      onCancel={cancel}
      onConfirm={confirm}
    >
      <div className="song-studio-options">
        <fieldset className="song-studio-group">
          <legend>{t("Singing method")}</legend>
          <div className="song-studio-methods">
            <label className={
              'song-studio-choice' + (engine === 'source-svc' ? ' selected' : '')
            } title={t("Follow the original words, melody and timing.")}>
              <input
                type="radio"
                name="song-cover-engine"
                value="source-svc"
                aria-label={t("Voice conversion")}
                aria-describedby={`${optionDescriptionId}-source-svc`}
                checked={engine === 'source-svc'}
                onChange={() => {
                  setEngine('source-svc')
                  setMetadataError(null)
                }}
              />
              <span className="song-studio-choice-heading">
                <Icon name="music" />
                <span className="song-studio-choice-check"><Icon name="check" /></span>
              </span>
              <span className="song-studio-choice-copy">
                <strong>{t("Voice conversion")}</strong>
                <span id={`${optionDescriptionId}-source-svc`} className="sr-only">{t("Follow the original words, melody and timing.")}</span>
              </span>
            </label>
            <label className={
              'song-studio-choice' + (engine === 'lyrics-svs' ? ' selected' : '')
            } title={t("Sing from synced Mandarin lyrics.")}>
              <input
                type="radio"
                name="song-cover-engine"
                value="lyrics-svs"
                aria-label={t("Lyrics-driven singing")}
                aria-describedby={`${optionDescriptionId}-lyrics-svs`}
                checked={engine === 'lyrics-svs'}
                onChange={() => {
                  setEngine('lyrics-svs')
                  setVocalArrangement('entire')
                  setMetadataError(null)
                }}
              />
              <span className="song-studio-choice-heading">
                <Icon name="captions" />
                <span className="song-studio-choice-check"><Icon name="check" /></span>
              </span>
              <span className="song-studio-choice-copy">
                <strong>{t("Lyrics-driven singing")}</strong>
                <span id={`${optionDescriptionId}-lyrics-svs`} className="sr-only">{t("Sing from synced Mandarin lyrics.")}</span>
              </span>
            </label>
            <label className={
              'song-studio-choice' + (engine === 'legacy-svc' ? ' selected' : '')
            } title={t("Use the previous local voice model.")}>
              <input
                type="radio"
                name="song-cover-engine"
                value="legacy-svc"
                aria-label={t("Legacy RVC")}
                aria-describedby={`${optionDescriptionId}-legacy-svc`}
                checked={engine === 'legacy-svc'}
                onChange={() => {
                  setEngine('legacy-svc')
                  setMetadataError(null)
                }}
              />
              <span className="song-studio-choice-heading">
                <Icon name="microphone" />
                <span className="song-studio-choice-check"><Icon name="check" /></span>
              </span>
              <span className="song-studio-choice-copy">
                <strong>{t("Legacy RVC")}</strong>
                <span id={`${optionDescriptionId}-legacy-svc`} className="sr-only">{t("Use the previous local voice model.")}</span>
              </span>
            </label>
          </div>
          {engine === 'legacy-svc' && (
            <HelpHint label={t("About voice quality")}>{t("Offline conversion using the source vocal as a pronunciation and timing guide. Quality depends on the local private model; sustained low or high notes may sound hoarse or electronic.")}</HelpHint>
          )}
        </fieldset>

        {engine === 'source-svc' && (
          <fieldset className="song-studio-group">
            <legend>{t("Singing reference")}</legend>
            <div className="song-studio-arrangements song-studio-references">
              {([
                ['fairy', t("妖精小姐的魔法邀约")],
                ['elysia', 'Elysia'],
              ] as const).map(([reference, label]) => (
                <label
                  key={reference}
                  className={'song-studio-arrangement' + (singingReference === reference ? ' selected' : '')}
                >
                  <input
                    type="radio"
                    name="song-cover-reference"
                    value={reference}
                    checked={singingReference === reference}
                    onChange={() => { setSingingReference(reference) }}
                  />
                  <span><strong>{label}</strong></span>
                </label>
              ))}
            </div>
          </fieldset>
        )}

        {engine !== 'lyrics-svs' && (
          <fieldset className="song-studio-group">
            <legend>{t("Vocal arrangement")}</legend>
            <div className="song-studio-arrangements">
              <label className={
                'song-studio-arrangement' + (vocalArrangement === 'entire' ? ' selected' : '')
              } title={t("Convert the full vocal track. Overlapping singers can blend.")}>
                <input
                  type="radio"
                  name="song-cover-arrangement"
                  value="entire"
                  aria-label={t("Entire vocal track")}
                  aria-describedby={`${optionDescriptionId}-entire`}
                  checked={vocalArrangement === 'entire'}
                  onChange={() => { setVocalArrangement('entire') }}
                />
                <span>
                  <strong>{t("Entire vocal track")}</strong>
                  <span id={`${optionDescriptionId}-entire`} className="sr-only">{t("Convert the full vocal track. Overlapping singers can blend.")}</span>
                </span>
              </label>
              <label className={
                'song-studio-arrangement' + (vocalArrangement === 'lead' ? ' selected' : '')
              } title={t("Use prepared stems to keep the original voices in your backing mix.")}>
                <input
                  type="radio"
                  name="song-cover-arrangement"
                  value="lead"
                  aria-label={t("Lead only, keep other singers")}
                  aria-describedby={`${optionDescriptionId}-lead`}
                  checked={vocalArrangement === 'lead'}
                  onChange={() => {
                    setVocalArrangement('lead')
                    setSourceMode('stems')
                  }}
                />
                <span>
                  <strong>{t("Lead only, keep other singers")}</strong>
                  <span id={`${optionDescriptionId}-lead`} className="sr-only">{t("Use prepared stems to keep the original voices in your backing mix.")}</span>
                </span>
              </label>
            </div>
          </fieldset>
        )}

        <fieldset className="song-studio-group">
          <legend>{t("Audio source")}</legend>
          <div className={'song-studio-sources' + (leadOnly ? ' song-studio-sources-prepared' : '')}>
            {!leadOnly && (
              <label className={
                'song-studio-source' + (sourceMode === 'song' ? ' selected' : '')
              }>
                <input
                  type="radio"
                  name="song-cover-source"
                  value="song"
                  checked={sourceMode === 'song'}
                  onChange={() => { setSourceMode('song') }}
                />
                <Icon name="music" />
                <strong>{t("Complete song")}</strong>
              </label>
            )}
            <label className={
              'song-studio-source' + (sourceMode === 'stems' ? ' selected' : '')
            }>
              <input
                type="radio"
                name="song-cover-source"
                value="stems"
                checked={sourceMode === 'stems'}
                onChange={() => { setSourceMode('stems') }}
              />
              <Icon name="speaker" />
              <strong>{leadOnly ? t("Isolated lead + backing mix") : t("Vocals + accompaniment")}</strong>
            </label>
          </div>
          <p className="song-studio-hint">
            {leadOnly
              ? t("Choose an isolated lead vocal first, then a time-aligned backing mix containing the instruments, original harmonies and other singers, with the lead removed.")
              : sourceMode === 'song'
              ? t("Choose one audio file. Elysia separates its vocal and music.")
              : t("Choose isolated vocals first, then the accompaniment file.")}
          </p>
        </fieldset>

        <fieldset
          className="song-studio-group song-studio-key-group"
          aria-describedby={pitchDescriptionId}
        >
          <legend>{t("Song key")}</legend>
          <div className="song-studio-key-controls">
            <button
              type="button"
              className="song-studio-key-step"
              aria-label={t("Lower song key one semitone")}
              disabled={keyShiftSemitones === -12}
              onClick={() => { changeKey(keyShiftSemitones - 1) }}
            >
              <span aria-hidden="true">−</span>
            </button>
            <div className="song-studio-key-value" aria-live="polite">
              <strong>{keyShiftSemitones === 0
                ? t("Original key")
                : t(Math.abs(keyShiftSemitones) === 1 ? "{direction}{count} semitone" : "{direction}{count} semitones", { direction: keyShiftSemitones > 0 ? '+' : '−', count: Math.abs(keyShiftSemitones) })}</strong>
            </div>
            <button
              type="button"
              className="song-studio-key-step"
              aria-label={t("Raise song key one semitone")}
              disabled={keyShiftSemitones === 12}
              onClick={() => { changeKey(keyShiftSemitones + 1) }}
            >
              <Icon name="plus" />
            </button>
          </div>
          <input
            type="range"
            className="song-studio-key-slider"
            name="song-cover-key"
            aria-label={t("Song key adjustment")}
            aria-describedby={pitchDescriptionId}
            aria-valuetext={keyShiftSemitones === 0
              ? t("Original key")
              : keyShiftSemitones < 0
                ? t(Math.abs(keyShiftSemitones) === 1 ? "Lowered {count} semitone" : "Lowered {count} semitones", { count: Math.abs(keyShiftSemitones) })
                : t(Math.abs(keyShiftSemitones) === 1 ? "Raised {count} semitone" : "Raised {count} semitones", { count: Math.abs(keyShiftSemitones) })}
            min={-12}
            max={12}
            step={1}
            value={keyShiftSemitones}
            onChange={(event) => { changeKey(Number(event.currentTarget.value)) }}
          />
          <div className="song-studio-key-scale">
            <span>−12</span>
            <button
              type="button"
              disabled={keyShiftSemitones === 0}
              onClick={() => { changeKey(0) }}
            >{t("Reset to original")}</button>
            <span>+12</span>
          </div>
          <label className="song-studio-accompaniment">
            <input
              type="checkbox"
              name="song-cover-shift-accompaniment"
              checked={shiftAccompaniment}
              onChange={(event) => {
                setShiftAccompaniment(event.currentTarget.checked)
              }}
            />
            <span>
              <strong>{t("Shift accompaniment with vocal")}</strong>
            </span>
          </label>
          {leadOnly && (
            <p className="song-studio-hint">
              {t("Use Original key to keep the other singers at their original pitch. Shifting the accompaniment also shifts the voices in the backing mix.")}</p>
          )}
          <HelpHint id={pitchDescriptionId} label={t("About key changes")}>{t("Original key follows the source melody most closely. A key shift always moves the lead; the option above decides whether the accompaniment follows it. Use the smallest shift that fits; larger shifts, especially the 12-semitone octave, can increase vocal artifacts. It is a range tradeoff, not an artifact repair") + (engine === 'legacy-svc' ? t(", and may soften transients") : '') + '. ' + t("Turn off accompaniment shifting to keep the music at its original key. Non-octave vocal shifts may then sound out of key.")}</HelpHint>
        </fieldset>

        {engine === 'lyrics-svs' && (
          <fieldset
            className="song-studio-group song-studio-lyrics"
            aria-describedby={metadataDescriptionId}
          >
            <legend>{t("Lyrics lookup ")}<span>{t("Optional")}</span></legend>
            <div className="song-studio-metadata-fields">
              <label>
                <span>{t("Song title")}</span>
                <input
                  type="text"
                  value={songTitle}
                  maxLength={256}
                  autoComplete="off"
                  placeholder={t("From the audio file")}
                  onChange={(event) => {
                    setSongTitle(event.currentTarget.value)
                    setMetadataError(null)
                  }}
                />
              </label>
              <label>
                <span>{t("Artist")}</span>
                <input
                  type="text"
                  value={songArtist}
                  maxLength={256}
                  autoComplete="off"
                  placeholder={t("From the audio file")}
                  onChange={(event) => {
                    setSongArtist(event.currentTarget.value)
                    setMetadataError(null)
                  }}
                />
              </label>
            </div>
            <p className="song-studio-hint" id={metadataDescriptionId}>
              {t("Mandarin lyrics only. Leave both blank to use audio tags.")}{' '}{t("LRCLIB receives the title, artist, and rounded duration to find lyrics; your audio is never uploaded.")}</p>
            <HelpHint label={t("When to add a title and artist")}>{t("A recognized Artist/Title filename can also identify a song. Generic or ambiguous names need both fields.")}</HelpHint>
          </fieldset>
        )}
      </div>
    </ChatActionDialog>
  )
}
