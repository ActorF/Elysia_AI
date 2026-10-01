/** Verify bounded amplitude-to-mouth quantization without exposing audio data. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  advanceSpeechMouthEnvelope,
  CLOSED_SPEECH_MOUTH_ENVELOPE,
} from '../electron/speech-mouth.ts'

function samplesAt(amplitude) {
  const offset = Math.round(Math.max(0, Math.min(1, amplitude)) * 127)
  return Uint8Array.from(
    { length: 128 },
    (_, index) => 128 + (index % 2 === 0 ? offset : -offset),
  )
}

function settleAt(amplitude, gain = 1, frames = 4) {
  let state = CLOSED_SPEECH_MOUTH_ENVELOPE
  for (let index = 0; index < frames; index += 1) {
    state = advanceSpeechMouthEnvelope(samplesAt(amplitude), gain, state)
  }
  return state
}

test('quantizes silence and increasing amplitudes into four closed cues', () => {
  assert.equal(settleAt(0).cue, 'closed')
  assert.equal(settleAt(0.04).cue, 'small')
  assert.equal(settleAt(0.1).cue, 'medium')
  assert.equal(settleAt(0.24).cue, 'wide')
})

test('applies actual output gain and keeps muted playback visually closed', () => {
  assert.equal(settleAt(0.8, 0).cue, 'closed')
  assert.equal(settleAt(0.24, 0.25).cue, 'small')
  assert.equal(settleAt(0.24, 1).cue, 'wide')
})

test('uses release hysteresis instead of flickering at adjacent boundaries', () => {
  const loud = settleAt(0.24)
  const firstRelease = advanceSpeechMouthEnvelope(samplesAt(0.1), 1, loud)
  assert.equal(firstRelease.cue, 'wide')

  let released = firstRelease
  for (let index = 0; index < 8; index += 1) {
    released = advanceSpeechMouthEnvelope(samplesAt(0), 1, released)
  }
  assert.equal(released.cue, 'closed')
})

test('fails malformed gain or prior state visually closed', () => {
  assert.equal(
    advanceSpeechMouthEnvelope(samplesAt(0.5), Number.NaN, {
      cue: 'wide',
      value: 0.5,
    }),
    CLOSED_SPEECH_MOUTH_ENVELOPE,
  )
  assert.equal(
    advanceSpeechMouthEnvelope(samplesAt(0.5), 1, {
      cue: 'wide',
      value: 2,
    }),
    CLOSED_SPEECH_MOUTH_ENVELOPE,
  )
})
