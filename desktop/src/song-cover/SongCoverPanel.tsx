/**
 * @fileoverview Present the Main-owned Song Cover job as a compact music card.
 * Stage and progress are observational; playback, cancellation, and export stay
 * with the existing privileged callbacks and never create a second audio owner.
 */

import { useRef, useState, type CSSProperties, type RefObject } from 'react'
import type { SongCoverState } from '../../electron/song-cover-contracts.ts'
import { Icon } from '../design-system/Icon.tsx'
import { isSongCoverBusyStage } from './song-cover-stage.ts'

import './SongCover.css'

interface SongCoverPanelProps {
  state: SongCoverState
  playbackDisabled: boolean
  hideButtonRef: RefObject<HTMLButtonElement | null>
  onCancel(): void
  onExport(): void
  onHide(): void
  onPlay(): void
  onSeek(positionSeconds: number): Promise<void>
  onStop(): void
}

function playbackTime(seconds: number): string {
  const wholeSeconds = Math.max(0, Math.floor(seconds))
  return `${Math.floor(wholeSeconds / 60)}:${String(wholeSeconds % 60).padStart(2, '0')}`
}

function keyLabel(shift: SongCoverState['keyShiftSemitones']): string {
  if (shift === null) return 'Key pending'
  if (shift === 0) return 'Original key'
  const semitones = Math.abs(shift)
  return `${shift > 0 ? 'Raised' : 'Lowered'} ${semitones} ${
    semitones === 1 ? 'semitone' : 'semitones'
  }`
}

function engineLabel(engine: SongCoverState['engine']): string {
  if (engine === 'lyrics-svs') return 'Lyrics-driven singing'
  if (engine === 'source-svc') return 'Voice conversion'
  if (engine === 'legacy-svc') return 'Legacy RVC'
  return 'Singing method pending'
}

function stageLabel(stage: SongCoverState['stage']): string {
  switch (stage) {
    case 'idle': return 'Song Cover'
    case 'validating': return 'Preparing audio'
    case 'separating': return 'Separating vocals'
    case 'transcribing': return 'Finding lyrics'
    case 'aligning': return 'Aligning lyrics'
    case 'synthesizing': return 'Singing'
    case 'converting': return 'Converting voice'
    case 'mixing': return 'Mixing your cover'
    case 'ready': return 'Ready to play'
    case 'playing': return 'Now playing'
    case 'cancelled': return 'Cancelled'
    case 'error': return 'Needs attention'
  }
}

function currentStep(stage: SongCoverState['stage']): number {
  if (stage === 'mixing') return 2
  if (stage === 'converting' || stage === 'synthesizing') return 1
  return 0
}

/** Render a cover's real lifecycle and available actions without synthetic time. */
export function SongCoverPanel({
  state,
  playbackDisabled,
  hideButtonRef,
  onCancel,
  onExport,
  onHide,
  onPlay,
  onSeek,
  onStop,
}: SongCoverPanelProps) {
  const running = isSongCoverBusyStage(state.stage)
  const playing = state.stage === 'playing'
  const [scrubPosition, setScrubPosition] = useState<number | null>(null)
  const draggingRef = useRef(false)
  const seekRevisionRef = useRef(0)
  const duration = state.durationSeconds
  const position = Math.min(duration, scrubPosition ?? state.positionSeconds)
  const step = currentStep(state.stage)

  async function commitSeek(seconds: number): Promise<void> {
    // Keep the thumb under the user's control until Main acknowledges the
    // seek. Older playback updates and earlier seek replies must not pull it
    // back while the user is choosing a newer position.
    const revision = ++seekRevisionRef.current
    setScrubPosition(seconds)
    try {
      await onSeek(seconds)
    } finally {
      if (revision === seekRevisionRef.current && !draggingRef.current) {
        setScrubPosition(null)
      }
    }
  }

  return (
    <section
      className={`song-cover-panel song-studio-player ${state.stage}`}
      aria-label="Song Cover"
    >
      <button
        ref={hideButtonRef}
        type="button"
        className="song-studio-hide"
        onClick={onHide}
        aria-label="Hide Song Cover"
        title="Hide player"
      >
        <Icon name="close" />
      </button>
      <div className="song-studio-record" aria-hidden="true">
        <span className="song-studio-record-disc"><Icon name="music" /></span>
      </div>
      <div className="song-studio-track">
        <div className="song-studio-track-heading">
          <span className="song-studio-eyebrow">Elysia · Song Cover</span>
          <span className="song-studio-stage">
            {playing && <span className="song-studio-equalizer" aria-hidden="true"><i /><i /><i /></span>}
            {stageLabel(state.stage)}
          </span>
        </div>
        <strong className="song-studio-track-name" title={state.sourceName ?? 'Song Cover'}>
          {state.sourceName ?? 'Song Cover'}
        </strong>
        <div className="song-studio-track-settings" aria-label="Song Cover settings">
          <span>{engineLabel(state.engine)}</span>
          <span>{keyLabel(state.keyShiftSemitones)}</span>
          {state.keyShiftSemitones !== null
            && state.keyShiftSemitones !== 0
            && state.shiftAccompaniment !== null && (
            <span>{state.shiftAccompaniment
              ? 'Vocal + accompaniment shift'
              : 'Vocal-only shift'}</span>
          )}
        </div>
        {state.sourceMode === 'stems' && state.accompanimentName !== null && (
          <span className="song-studio-track-accompaniment" title={state.accompanimentName}>
            Accompaniment · {state.accompanimentName}
          </span>
        )}
      </div>

      <div className="song-studio-player-body">
        {running && (
          <div className="song-studio-job-progress">
            <div className="song-studio-steps" aria-hidden="true">
              {['Prepare', 'Sing', 'Mix'].map((label, index) => (
                <span key={label} className={index <= step ? 'is-current' : ''}>
                  <i />{label}
                </span>
              ))}
              <span className="song-studio-percent">
                {state.progressPercent}%
              </span>
            </div>
            <progress
              max={100}
              value={state.progressPercent}
              aria-label="Song Cover progress"
            />
          </div>
        )}
        {state.outputAvailable && (
          <div className="song-studio-timeline">
            <input
              type="range"
              className="song-studio-seek"
              aria-label="Seek Song Cover"
              aria-valuetext={`${playbackTime(position)} of ${playbackTime(duration)}`}
              min={0}
              max={duration > 0 ? duration : 1}
              step={0.1}
              value={position}
              disabled={duration <= 0 || (!playing && playbackDisabled)}
              style={{ '--song-played': `${duration > 0 ? position / duration * 100 : 0}%` } as CSSProperties}
              onPointerDown={(event) => {
                if (event.button !== 0) return
                event.currentTarget.setPointerCapture(event.pointerId)
                draggingRef.current = true
                ++seekRevisionRef.current
              }}
              onChange={(event) => {
                const seconds = event.currentTarget.valueAsNumber
                if (draggingRef.current) setScrubPosition(seconds)
                else void commitSeek(seconds)
              }}
              onPointerUp={(event) => {
                draggingRef.current = false
                void commitSeek(event.currentTarget.valueAsNumber)
              }}
              onPointerCancel={() => {
                draggingRef.current = false
                setScrubPosition(null)
              }}
              onBlur={() => {
                if (draggingRef.current) {
                  draggingRef.current = false
                  setScrubPosition(null)
                }
              }}
            />
            <div className="song-studio-time" aria-label="Playback time">
              <span>{playbackTime(position)}</span>
              <span>{playbackTime(duration)}</span>
            </div>
          </div>
        )}
        {(running || !state.outputAvailable || state.error !== null) && <p
          className="song-studio-status"
          role={state.error === null ? 'status' : 'alert'}
          aria-live="polite"
        >
          {state.error ?? state.message ?? 'Choose another song to try again.'}
        </p>}
      </div>
      <div className="song-studio-player-actions">
        {running && (
          <button type="button" className="song-studio-action" onClick={onCancel} aria-label="Cancel Song Cover">
            <Icon name="close" /><span>Cancel</span>
          </button>
        )}
        {state.outputAvailable && (
          <>
            <button
              type="button"
              className="song-studio-action song-studio-action-primary"
              disabled={!playing && playbackDisabled}
              onClick={playing ? onStop : onPlay}
              aria-label={playing ? 'Stop Song Cover' : 'Play Song Cover'}
            >
              <Icon name={playing ? 'stop' : 'play'} />
              <span>{playing ? 'Stop' : 'Play cover'}</span>
            </button>
            <button
              type="button"
              className="song-studio-action"
              onClick={onExport}
              aria-label="Export Song Cover"
              title="Export lossless WAV"
            >
              <Icon name="download" /><span>Save WAV</span>
            </button>
          </>
        )}
      </div>
    </section>
  )
}
