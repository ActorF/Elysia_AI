/** Verify the closed character state contract and deterministic source priority. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  deriveApplicationCharacterState,
  deriveVoiceCharacterState,
  resolveCharacterState,
} from '../src/character/character-state.ts'

test('exposes every closed character state through semantic inputs', () => {
  assert.equal(deriveApplicationCharacterState({
    backendStatus: 'ready',
    chatActivity: 'idle',
    chatMode: 'chat',
    knowledgeActive: false,
    knowledgeError: false,
    waitingApproval: false,
  }).state, 'idle')
  assert.equal(deriveVoiceCharacterState('listening').state, 'listening')
  assert.equal(deriveApplicationCharacterState({
    backendStatus: 'ready',
    chatActivity: 'generating',
    chatMode: 'chat',
    knowledgeActive: false,
    knowledgeError: false,
    waitingApproval: false,
  }).state, 'thinking')
  assert.equal(deriveVoiceCharacterState('speaking').state, 'speaking')
  assert.equal(deriveApplicationCharacterState({
    backendStatus: 'ready',
    chatActivity: 'idle',
    chatMode: 'chat',
    knowledgeActive: true,
    knowledgeError: false,
    waitingApproval: false,
  }).state, 'working')
  assert.equal(deriveApplicationCharacterState({
    backendStatus: 'ready',
    chatActivity: 'idle',
    chatMode: 'chat',
    knowledgeActive: false,
    knowledgeError: false,
    waitingApproval: true,
  }).state, 'waiting_approval')
  assert.equal(deriveApplicationCharacterState({
    backendStatus: 'error',
    chatActivity: 'idle',
    chatMode: 'chat',
    knowledgeActive: false,
    knowledgeError: false,
    waitingApproval: false,
  }).state, 'error')
})

test('uses one order-independent priority for simultaneous activity', () => {
  const ordered = [
    'idle',
    'working',
    'thinking',
    'listening',
    'speaking',
    'waiting_approval',
    'error',
  ]
  assert.equal(resolveCharacterState(ordered).state, 'error')
  assert.equal(resolveCharacterState([...ordered].reverse()).state, 'error')
  assert.equal(resolveCharacterState([]).state, 'idle')
  assert.equal(resolveCharacterState(['working', 'thinking']).state, 'thinking')
  assert.equal(resolveCharacterState(['listening', 'speaking']).state, 'speaking')
  assert.equal(
    resolveCharacterState(['speaking', 'waiting_approval']).state,
    'waiting_approval',
  )
})

test('keeps errors and approval visible above application activity', () => {
  assert.equal(deriveApplicationCharacterState({
    backendStatus: 'error',
    chatActivity: 'generating',
    chatMode: 'work',
    knowledgeActive: true,
    knowledgeError: false,
    waitingApproval: true,
  }).state, 'error')
  assert.equal(deriveApplicationCharacterState({
    backendStatus: 'ready',
    chatActivity: 'error',
    chatMode: 'chat',
    knowledgeActive: true,
    knowledgeError: false,
    waitingApproval: true,
  }).state, 'error')
  assert.equal(deriveApplicationCharacterState({
    backendStatus: 'ready',
    chatActivity: 'generating',
    chatMode: 'work',
    knowledgeActive: true,
    knowledgeError: false,
    waitingApproval: true,
  }).state, 'waiting_approval')
})

test('distinguishes conversational generation from Work and Knowledge activity', () => {
  const base = {
    backendStatus: 'ready',
    chatActivity: 'generating',
    knowledgeActive: false,
    knowledgeError: false,
    waitingApproval: false,
  }
  assert.equal(deriveApplicationCharacterState({
    ...base,
    chatMode: 'chat',
  }).state, 'thinking')
  assert.equal(deriveApplicationCharacterState({
    ...base,
    chatMode: 'work',
  }).state, 'working')
  assert.equal(deriveApplicationCharacterState({
    ...base,
    chatActivity: 'idle',
    chatMode: 'chat',
    knowledgeActive: true,
  }).state, 'working')
  assert.equal(deriveApplicationCharacterState({
    ...base,
    chatMode: 'chat',
    knowledgeActive: true,
  }).state, 'thinking')
  assert.equal(deriveApplicationCharacterState({
    ...base,
    chatActivity: 'idle',
    chatMode: 'chat',
    knowledgeError: true,
  }).state, 'error')
})

test('maps only Voice primary lifecycle and keeps neutral review states idle', () => {
  const expected = new Map([
    ['ready', 'idle'],
    ['listening', 'listening'],
    ['transcribing', 'thinking'],
    ['reviewing', 'idle'],
    ['thinking', 'thinking'],
    ['speaking', 'speaking'],
    ['interrupting', 'listening'],
    ['cancelled', 'idle'],
    ['error', 'error'],
  ])
  for (const [voiceState, characterState] of expected) {
    assert.equal(
      deriveVoiceCharacterState(voiceState).state,
      characterState,
      voiceState,
    )
  }
  assert.equal(deriveVoiceCharacterState('reviewing', 'error').state, 'error')
  assert.equal(deriveVoiceCharacterState('speaking', 'stopped').state, 'error')
})

test('keeps recoverable Backend transitions neutral but stopped is actionable', () => {
  for (const backendStatus of [
    'starting',
    'handshaking',
    'initializing',
    'ready',
    'stopping',
  ]) {
    assert.equal(deriveApplicationCharacterState({
      backendStatus,
      chatActivity: 'idle',
      chatMode: 'chat',
      knowledgeActive: false,
      knowledgeError: false,
      waitingApproval: false,
    }).state, 'idle')
  }
  assert.equal(deriveApplicationCharacterState({
    backendStatus: 'stopped',
    chatActivity: 'idle',
    chatMode: 'chat',
    knowledgeActive: false,
    knowledgeError: false,
    waitingApproval: false,
  }).state, 'error')
})
