/** Verify deterministic Voice UI state precedence and duration formatting. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  deriveVoiceUiPresentation,
  formatVoiceSessionDuration,
  formatVoiceSessionDurationIso,
} from '../src/voice/voice-ui-state.ts'

const BASE_CAPTURE = Object.freeze({
  status: 'idle',
  sessionId: null,
  deviceId: null,
  level: 0,
  elapsedMs: 0,
  bufferedSampleCount: 0,
  voicedSampleCount: 0,
  completedSampleCount: 0,
  error: null,
})

/** Build a complete derivation input while allowing one test to change fields. */
function presentationInput(overrides = {}) {
  return {
    capture: BASE_CAPTURE,
    captureDisabledReason: null,
    interruptionState: null,
    microphoneMuted: false,
    sessionPhase: 'idle',
    submissionError: null,
    transcription: null,
    ...overrides,
  }
}

test('keeps reply lifecycle primary while interruption monitoring is secondary', () => {
  const thinking = deriveVoiceUiPresentation(presentationInput({
    interruptionState: 'monitoring',
    sessionPhase: 'thinking',
  }))
  assert.equal(thinking.primaryState, 'thinking')
  assert.equal(thinking.primaryLabel, 'Elysia is thinking')
  assert.equal(thinking.microphoneState, 'monitoring')
  assert.equal(thinking.microphoneLabel, 'Monitoring interruptions')

  const speaking = deriveVoiceUiPresentation(presentationInput({
    interruptionState: 'monitoring',
    sessionPhase: 'speaking',
  }))
  assert.equal(speaking.primaryState, 'speaking')
  assert.equal(speaking.primaryLabel, 'Elysia is speaking')
  assert.equal(speaking.microphoneState, 'monitoring')
})

test('keeps reply lifecycle primary when passive microphone monitoring fails', () => {
  const presentation = deriveVoiceUiPresentation(presentationInput({
    capture: {
      ...BASE_CAPTURE,
      status: 'error',
      error: {
        code: 'device-lost',
        message: 'The selected microphone disconnected.',
      },
    },
    sessionPhase: 'speaking',
  }))

  assert.equal(presentation.primaryState, 'speaking')
  assert.equal(presentation.primaryLabel, 'Elysia is speaking')
  assert.equal(presentation.microphoneState, 'unavailable')
  assert.equal(presentation.microphoneLabel, 'Microphone unavailable')
})

test('presents confirmed barge-in as interrupting with an active microphone', () => {
  const presentation = deriveVoiceUiPresentation(presentationInput({
    interruptionState: 'cancelling',
    sessionPhase: 'speaking',
  }))
  assert.equal(presentation.primaryState, 'interrupting')
  assert.equal(presentation.primaryLabel, 'Interrupting Elysia')
  assert.equal(presentation.microphoneState, 'monitoring')
  assert.equal(presentation.microphoneLabel, 'Microphone active')
})

test('keeps cancellation neutral and transcription failure actionable', () => {
  const cancelled = deriveVoiceUiPresentation(presentationInput({
    capture: { ...BASE_CAPTURE, status: 'cancelled' },
  }))
  assert.equal(cancelled.primaryState, 'cancelled')
  assert.equal(cancelled.primaryLabel, 'Capture cancelled')

  const failed = deriveVoiceUiPresentation(presentationInput({
    transcription: {
      phase: 'error',
      text: '',
      language: null,
      languageProbability: null,
      error: 'The local speech model stopped.',
      retryable: true,
    },
  }))
  assert.equal(failed.primaryState, 'error')
  assert.equal(failed.primaryLabel, 'Transcription failed')
  assert.equal(failed.primaryDescription, 'The local speech model stopped.')
})

test('shows microphone mute without hiding an active reply state', () => {
  const presentation = deriveVoiceUiPresentation(presentationInput({
    interruptionState: 'monitoring',
    microphoneMuted: true,
    sessionPhase: 'speaking',
  }))
  assert.equal(presentation.primaryState, 'speaking')
  assert.equal(presentation.microphoneState, 'muted')
  assert.equal(presentation.microphoneLabel, 'Microphone muted')
})

test('identifies final transcripts as a separate manual-review state', () => {
  const presentation = deriveVoiceUiPresentation(presentationInput({
    transcription: {
      phase: 'final',
      text: 'Review me before sending.',
      language: 'en',
      languageProbability: 0.98,
      error: null,
      retryable: false,
    },
  }))
  assert.equal(presentation.primaryState, 'reviewing')
  assert.equal(presentation.primaryLabel, 'Transcript ready')
  assert.match(presentation.primaryDescription, /Nothing enters Chat/u)
})

test('formats bounded session clocks without leaking invalid numeric values', () => {
  assert.equal(formatVoiceSessionDuration(Number.NaN), '00:00')
  assert.equal(formatVoiceSessionDuration(-1), '00:00')
  assert.equal(formatVoiceSessionDuration(65_999), '01:05')
  assert.equal(formatVoiceSessionDuration(3_661_999), '01:01:01')
  assert.equal(formatVoiceSessionDurationIso(65_999), 'PT65S')
})
