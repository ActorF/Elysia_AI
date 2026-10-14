/** Verify the call window boundary accepts only bounded presentation and exact-call controls. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  parseVoiceCallId,
  parseVoiceCallWindowAction,
  parseVoiceCallWindowState,
} from '../dist-electron/voice-call-contracts.js'

const CALL_ID = 'd95b7aaf-d40e-4208-9caa-191c80570d2c'
const STATE = {
  callId: CALL_ID,
  revision: 0,
  characterState: 'listening',
  emotion: 'caring',
  microphoneMuted: false,
  startedAtMs: 1_790_000_000_000,
  error: null,
  warning: null,
}

test('copies and freezes the complete bounded state without adding audio authority', () => {
  const parsed = parseVoiceCallWindowState(STATE)
  assert.deepEqual(parsed, STATE)
  assert.notEqual(parsed, STATE)
  assert.equal(Object.isFrozen(parsed), true)
  assert.deepEqual(Object.keys(parsed), Object.keys(STATE))
  assert.equal(parseVoiceCallId(CALL_ID), CALL_ID)
})

test('accepts every shared semantic state and emotion', () => {
  for (const characterState of [
    'idle', 'listening', 'thinking', 'speaking', 'working', 'waiting_approval', 'error',
  ]) {
    assert.equal(parseVoiceCallWindowState({ ...STATE, characterState }).characterState, characterState)
  }
  for (const emotion of [
    'neutral', 'happy', 'sad', 'caring', 'moved', 'playful', 'affectionate',
    'teasing', 'serious', 'surprised',
  ]) {
    assert.equal(parseVoiceCallWindowState({ ...STATE, emotion }).emotion, emotion)
  }
})

test('rejects malformed schemas, non-finite times and non-monotonic revision values', () => {
  const missingWarning = { ...STATE }
  delete missingWarning.warning
  for (const value of [
    null, [], 'state', missingWarning,
    { ...STATE, imagePath: 'D:\\private.webp' },
    { ...STATE, [Symbol('extra')]: true },
    { ...STATE, callId: 'another-call' },
    { ...STATE, revision: -1 },
    { ...STATE, revision: 0.5 },
    { ...STATE, revision: Number.MAX_SAFE_INTEGER + 1 },
    { ...STATE, characterState: 'capturing' },
    { ...STATE, emotion: 'unknown' },
    { ...STATE, microphoneMuted: 0 },
    { ...STATE, startedAtMs: -1 },
    { ...STATE, startedAtMs: Number.NaN },
    { ...STATE, startedAtMs: Number.POSITIVE_INFINITY },
    { ...STATE, error: {} },
    { ...STATE, warning: undefined },
  ]) {
    assert.throws(() => parseVoiceCallWindowState(value), /Voice Call/u)
  }
})

test('bounds errors and warnings in Unicode code points', () => {
  const text = '🌸'.repeat(1_024)
  assert.equal(parseVoiceCallWindowState({ ...STATE, error: text, warning: text }).error, text)
  assert.throws(() => parseVoiceCallWindowState({ ...STATE, error: `${text}a` }), /invalid/u)
  assert.throws(() => parseVoiceCallWindowState({ ...STATE, warning: `${text}a` }), /invalid/u)
})

test('allows only explicit mute and end controls with the required exact call identity', () => {
  for (const value of [
    { callId: CALL_ID, kind: 'mute', muted: false },
    { callId: CALL_ID, kind: 'mute', muted: true },
    { callId: CALL_ID, kind: 'end' },
  ]) {
    const parsed = parseVoiceCallWindowAction(value)
    assert.deepEqual(parsed, value)
    assert.equal(Object.isFrozen(parsed), true)
  }
  for (const value of [
    null, [], { callId: CALL_ID, kind: 'mute' },
    { callId: CALL_ID, kind: 'mute', muted: 'yes' },
    { callId: CALL_ID, kind: 'end', muted: false },
    { callId: CALL_ID, kind: 'end', sourcePath: 'private' },
    { callId: CALL_ID, kind: 'capture' },
    { callId: 'old-call', kind: 'end' },
  ]) {
    assert.throws(() => parseVoiceCallWindowAction(value), /Voice Call/u)
  }
})
