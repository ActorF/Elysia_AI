/** Verify Voice Session ownership, lifecycle, and asynchronous race handling. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  VoiceSessionController,
  VoiceSessionTransitionError,
} from '../src/voice/voice-session-controller.ts'

/** Create an active controller bound to the default test conversation. */
function createBoundController(projectId = 'project_alpha') {
  const controller = new VoiceSessionController()
  controller.bind({ chatId: 'chat_alpha', projectId })
  return controller
}

/** Read the immutable correlation owner for the currently bound Session. */
function currentOwner(controller) {
  const snapshot = controller.getSnapshot()
  assert.notEqual(snapshot.binding, null)
  return {
    epoch: snapshot.epoch,
    chatId: snapshot.binding.chatId,
    projectId: snapshot.binding.projectId,
  }
}

/** Reach a final, editable transcript and return its exact asynchronous owner. */
function reachFinalTranscript(
  controller,
  {
    captureSessionId = 'voice_capture_alpha',
    requestId = 'stt_request_alpha',
    acknowledgeFirst = true,
    text = 'Hello from the microphone.',
  } = {},
) {
  const owner = currentOwner(controller)
  controller.startListening(captureSessionId)
  assert.equal(controller.acceptCaptureComplete({
    ...owner,
    captureSessionId,
  }), true)
  const acknowledgement = { ...owner, captureSessionId, requestId }
  if (acknowledgeFirst) {
    assert.equal(
      controller.acknowledgeTranscription(acknowledgement),
      true,
    )
  }
  assert.equal(controller.acceptTranscriptionFinal({
    ...acknowledgement,
    text,
    language: 'en',
    languageProbability: 0.99,
  }), true)
  return { owner, acknowledgement }
}

/** Reach one thinking Chat turn through the public transcript confirmation API. */
function reachThinkingTurn(
  controller,
  {
    operationId = 'chat_operation_alpha',
    requestId = 'chat_request_alpha',
    speechExpected = true,
    acknowledgeChat = true,
  } = {},
) {
  const { owner } = reachFinalTranscript(controller)
  const confirmed = controller.confirmTranscript(operationId, speechExpected)
  assert.deepEqual(confirmed, {
    ...owner,
    operationId,
    text: 'Hello from the microphone.',
  })
  const chatOwner = { ...owner, operationId, requestId }
  if (acknowledgeChat) {
    assert.equal(controller.acknowledgeChatRequest(chatOwner), true)
  }
  return { owner, chatOwner, operationId, requestId }
}

test('runs the five phases and accepts STT and Chat acknowledgements after events', () => {
  const controller = createBoundController()
  const { owner, acknowledgement } = reachFinalTranscript(controller, {
    acknowledgeFirst: false,
  })

  assert.equal(controller.getSnapshot().phase, 'transcribing')
  assert.equal(controller.getSnapshot().transcript?.text, 'Hello from the microphone.')
  controller.updateTranscript('Edited final transcript.')
  assert.equal(
    controller.acknowledgeTranscription(acknowledgement),
    true,
  )
  assert.equal(controller.acknowledgeTranscription({
    ...acknowledgement,
    requestId: 'stt_request_wrong',
  }), false)

  const operationId = 'chat_operation_race'
  const requestId = 'chat_request_race'
  assert.deepEqual(controller.confirmTranscript(operationId, true), {
    ...owner,
    operationId,
    text: 'Edited final transcript.',
  })
  assert.equal(controller.getSnapshot().phase, 'thinking')

  const chatOwner = { ...owner, operationId, requestId }
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'playing',
    sequence: 0,
  }), false)
  assert.equal(controller.getSnapshot().phase, 'thinking')
  assert.equal(controller.acknowledgeChatRequest(chatOwner), true)
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'playing',
    sequence: 0,
  }), true)
  assert.equal(controller.getSnapshot().phase, 'speaking')
  assert.equal(controller.acceptChatTerminal({
    ...chatOwner,
    outcome: 'completed',
  }), true)
  assert.equal(controller.getSnapshot().phase, 'speaking')
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'played',
    sequence: 0,
  }), true)
  assert.equal(controller.getSnapshot().phase, 'thinking')
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'terminal',
    state: 'completed',
  }), true)

  assert.deepEqual(controller.getSnapshot(), {
    active: true,
    epoch: owner.epoch,
    phase: 'idle',
    binding: { chatId: 'chat_alpha', projectId: 'project_alpha' },
    captureSessionId: null,
    transcriptionRequestId: null,
    chatOperationId: null,
    chatRequestId: null,
    interruptionCaptureSessionId: null,
    transcript: null,
    chatTerminal: null,
    speechExpected: false,
    speechTerminal: null,
    activeSpeechSequence: null,
    speechPlayed: false,
    skippedSpeechCount: 0,
    lastTurn: {
      outcome: 'completed',
      speechPlayed: true,
      skippedSpeechCount: 0,
    },
  })
})

test('settles when speech ends first and every synthesis item was skipped', () => {
  const controller = createBoundController(null)
  const { chatOwner } = reachThinkingTurn(controller)

  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'skipped',
    sequence: 0,
  }), true)
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'terminal',
    state: 'completed',
  }), true)
  assert.equal(controller.getSnapshot().phase, 'thinking')
  assert.equal(controller.acceptChatTerminal({
    ...chatOwner,
    outcome: 'completed',
  }), true)
  assert.equal(controller.getSnapshot().phase, 'idle')
  assert.deepEqual(controller.getSnapshot().lastTurn, {
    outcome: 'completed',
    speechPlayed: false,
    skippedSpeechCount: 1,
  })
})

test('text-only confirmation completes without waiting for speech', () => {
  const controller = createBoundController()
  const { chatOwner } = reachThinkingTurn(controller, {
    speechExpected: false,
  })

  assert.equal(controller.getSnapshot().speechExpected, false)
  assert.equal(controller.getSnapshot().speechTerminal, 'completed')
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'playing',
    sequence: 0,
  }), false)
  assert.equal(controller.acceptChatTerminal({
    ...chatOwner,
    outcome: 'completed',
  }), true)
  assert.equal(controller.getSnapshot().phase, 'idle')
  assert.deepEqual(controller.getSnapshot().lastTurn, {
    outcome: 'completed',
    speechPlayed: false,
    skippedSpeechCount: 0,
  })
})

test('speech capability loss before Chat acknowledgement cannot strand a turn', () => {
  const controller = createBoundController()
  const { owner, operationId, requestId } = reachThinkingTurn(controller, {
    acknowledgeChat: false,
  })
  const chatOwner = { ...owner, operationId, requestId }

  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'playing',
    sequence: 0,
  }), false)
  assert.equal(controller.getSnapshot().phase, 'thinking')
  assert.equal(controller.acceptSpeechUnavailable({
    ...owner,
    operationId,
  }), true)
  assert.equal(controller.getSnapshot().speechTerminal, 'cancelled')
  assert.equal(controller.getSnapshot().phase, 'thinking')
  assert.equal(controller.acceptChatTerminal({
    ...chatOwner,
    outcome: 'completed',
  }), true)
  assert.equal(controller.getSnapshot().phase, 'idle')
  assert.equal(controller.acknowledgeChatRequest(chatOwner), true)
  assert.equal(controller.acknowledgeChatRequest({
    ...chatOwner,
    requestId: 'chat_request_wrong',
  }), false)
})

test('terminal-before-acknowledgement remains exact after STT failure', () => {
  const controller = createBoundController()
  const owner = currentOwner(controller)
  const captureSessionId = 'voice_capture_failure'
  const requestId = 'stt_request_failure'
  controller.startListening(captureSessionId)
  assert.equal(controller.acceptCaptureComplete({
    ...owner,
    captureSessionId,
  }), true)
  assert.equal(controller.acceptTranscriptionFailure({
    ...owner,
    captureSessionId,
    requestId,
    outcome: 'failed',
  }), true)
  assert.equal(controller.getSnapshot().phase, 'idle')
  assert.equal(controller.acknowledgeTranscription({
    ...owner,
    captureSessionId,
    requestId,
  }), true)
  assert.equal(controller.acknowledgeTranscription({
    ...owner,
    captureSessionId,
    requestId: 'stt_request_other',
  }), false)
})

test('rejecting synchronous Chat dispatch restores the editable transcript', () => {
  for (const speechExpected of [true, false]) {
    const controller = createBoundController()
    const { owner } = reachFinalTranscript(controller)
    const operationId = `chat_operation_reject_${speechExpected}`
    controller.confirmTranscript(operationId, speechExpected)
    if (speechExpected) {
      assert.equal(controller.acceptSpeechUnavailable({
        ...owner,
        operationId,
      }), true)
    }

    assert.equal(controller.rejectChatStart(owner, operationId), true)
    assert.equal(controller.getSnapshot().phase, 'transcribing')
    assert.equal(
      controller.getSnapshot().transcript?.text,
      'Hello from the microphone.',
    )
    controller.updateTranscript('Retry this transcript.')
    assert.equal(controller.getSnapshot().transcript?.text, 'Retry this transcript.')
  }
})

test('invalid final transcription payloads cannot pin request ownership', () => {
  const controller = createBoundController()
  const owner = currentOwner(controller)
  const captureSessionId = 'voice_capture_invalid_final'
  controller.startListening(captureSessionId)
  assert.equal(controller.acceptCaptureComplete({
    ...owner,
    captureSessionId,
  }), true)

  assert.equal(controller.acceptTranscriptionFinal({
    ...owner,
    captureSessionId,
    requestId: 'stt_request_forged',
    text: 'Invalid language must not claim this turn.',
    language: 'fr',
    languageProbability: 0.9,
  }), false)
  assert.equal(controller.getSnapshot().transcriptionRequestId, null)
  assert.equal(controller.acceptTranscriptionFinal({
    ...owner,
    captureSessionId,
    requestId: 'stt_request_real',
    text: 'The real final transcript remains admissible.',
    language: 'en',
    languageProbability: 0.99,
  }), true)
  assert.equal(
    controller.getSnapshot().transcriptionRequestId,
    'stt_request_real',
  )
})

test('cancel and hang-up invalidate late events and remain idempotent', () => {
  const controller = createBoundController()
  const { owner, acknowledgement } = reachFinalTranscript(controller)
  const cancellation = controller.cancel()

  assert.deepEqual(cancellation, {
    owner,
    captureSessionId: acknowledgement.captureSessionId,
    transcriptionRequestId: acknowledgement.requestId,
    chatOperationId: null,
    chatRequestId: null,
  })
  assert.equal(controller.getSnapshot().phase, 'idle')
  assert.equal(controller.getSnapshot().epoch, owner.epoch + 1)
  assert.equal(controller.acceptTranscriptionFinal({
    ...acknowledgement,
    text: 'Late transcript.',
    language: 'en',
    languageProbability: 1,
  }), false)

  const nextOwner = currentOwner(controller)
  controller.startListening('voice_capture_next')
  const hangUp = controller.hangUp()
  assert.deepEqual(hangUp, {
    owner: nextOwner,
    captureSessionId: 'voice_capture_next',
    transcriptionRequestId: null,
    chatOperationId: null,
    chatRequestId: null,
  })
  assert.equal(controller.getSnapshot().active, false)
  assert.equal(controller.getSnapshot().binding, null)
  assert.equal(controller.acceptCaptureComplete({
    ...nextOwner,
    captureSessionId: 'voice_capture_next',
  }), false)
  assert.deepEqual(controller.hangUp(), {
    owner: null,
    captureSessionId: null,
    transcriptionRequestId: null,
    chatOperationId: null,
    chatRequestId: null,
  })
})

test('arms and disarms only the exact passive interruption capture', () => {
  const controller = createBoundController()
  const { owner, operationId } = reachThinkingTurn(controller)
  const interruption = {
    ...owner,
    operationId,
    captureSessionId: 'voice_interruption_exact',
  }

  assert.equal(controller.armInterruption({
    ...interruption,
    operationId: 'chat_operation_wrong',
  }), false)
  assert.equal(controller.armInterruption({
    ...interruption,
    epoch: owner.epoch + 1,
  }), false)
  assert.equal(controller.armInterruption(interruption), true)
  assert.equal(controller.armInterruption(interruption), true)
  assert.equal(
    controller.getSnapshot().interruptionCaptureSessionId,
    interruption.captureSessionId,
  )
  assert.equal(controller.armInterruption({
    ...interruption,
    captureSessionId: 'voice_interruption_other',
  }), false)
  assert.equal(controller.rejectInterruptionStart({
    ...interruption,
    captureSessionId: 'voice_interruption_other',
  }), false)
  assert.equal(controller.rejectInterruptionStart(interruption), true)
  assert.equal(controller.getSnapshot().interruptionCaptureSessionId, null)
  assert.equal(controller.rejectInterruptionStart(interruption), false)
  assert.equal(controller.armInterruption(interruption), true)
})

test('interrupts a pre-acknowledgement Chat turn without admitting late callbacks', () => {
  const controller = createBoundController()
  const { owner, operationId, requestId } = reachThinkingTurn(controller, {
    acknowledgeChat: false,
  })
  const interruption = {
    ...owner,
    operationId,
    captureSessionId: 'voice_interruption_pre_ack',
  }
  assert.equal(controller.armInterruption(interruption), true)

  assert.deepEqual(controller.acceptInterruption(interruption), {
    owner,
    captureSessionId: null,
    transcriptionRequestId: null,
    chatOperationId: operationId,
    chatRequestId: null,
  })
  const nextOwner = currentOwner(controller)
  assert.deepEqual(controller.getSnapshot().lastTurn, {
    outcome: 'cancelled',
    speechPlayed: false,
    skippedSpeechCount: 0,
  })
  assert.equal(controller.getSnapshot().phase, 'listening')
  assert.equal(
    controller.getSnapshot().captureSessionId,
    interruption.captureSessionId,
  )
  assert.equal(controller.getSnapshot().interruptionCaptureSessionId, null)

  const lateChatOwner = { ...owner, operationId, requestId }
  assert.equal(controller.acknowledgeChatRequest(lateChatOwner), false)
  assert.equal(controller.acceptChatTerminal({
    ...lateChatOwner,
    outcome: 'cancelled',
  }), false)
  assert.equal(controller.acceptSpeechStatus({
    ...lateChatOwner,
    kind: 'playing',
    sequence: 0,
  }), false)
  assert.equal(controller.acceptCaptureComplete({
    ...nextOwner,
    captureSessionId: interruption.captureSessionId,
  }), true)
})

test('interrupts speaking while returning exact Chat cancellation ownership', () => {
  const controller = createBoundController()
  const { owner, chatOwner, operationId } = reachThinkingTurn(controller)
  const interruption = {
    ...owner,
    operationId,
    captureSessionId: 'voice_interruption_speaking',
  }
  assert.equal(controller.armInterruption(interruption), true)
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'playing',
    sequence: 0,
  }), true)
  assert.equal(controller.getSnapshot().phase, 'speaking')

  assert.deepEqual(controller.acceptInterruption(interruption), {
    owner,
    captureSessionId: null,
    transcriptionRequestId: null,
    chatOperationId: operationId,
    chatRequestId: chatOwner.requestId,
  })
  assert.deepEqual(controller.getSnapshot().lastTurn, {
    outcome: 'cancelled',
    speechPlayed: true,
    skippedSpeechCount: 0,
  })
  assert.equal(controller.acceptInterruption(interruption), null)
})

test('rejects illegal transitions, wrong owners, and out-of-order speech', () => {
  const controller = new VoiceSessionController()
  assert.throws(
    () => controller.startListening('voice_capture_unbound'),
    VoiceSessionTransitionError,
  )
  controller.bind({ chatId: 'chat_exact', projectId: 'project_exact' })
  const owner = currentOwner(controller)
  controller.startListening('voice_capture_exact')
  assert.throws(
    () => controller.bind({ chatId: 'chat_other', projectId: null }),
    VoiceSessionTransitionError,
  )
  assert.equal(controller.acceptCaptureComplete({
    ...owner,
    projectId: 'project_wrong',
    captureSessionId: 'voice_capture_exact',
  }), false)
  assert.equal(controller.acceptCaptureComplete({
    ...owner,
    captureSessionId: 'voice_capture_exact',
  }), true)
  assert.throws(
    () => controller.confirmTranscript('chat_operation_early', true),
    VoiceSessionTransitionError,
  )
  assert.throws(
    () => controller.updateTranscript('Too early.'),
    VoiceSessionTransitionError,
  )
  assert.equal(controller.acceptTranscriptionFinal({
    ...owner,
    captureSessionId: 'voice_capture_exact',
    requestId: 'stt_request_exact',
    text: 'Exact owner.',
    language: 'en',
    languageProbability: 1,
  }), true)
  controller.confirmTranscript('chat_operation_exact', true)
  const chatOwner = {
    ...owner,
    operationId: 'chat_operation_exact',
    requestId: 'chat_request_exact',
  }
  assert.equal(controller.acknowledgeChatRequest(chatOwner), true)
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'played',
    sequence: 0,
  }), false)
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'playing',
    sequence: 1,
  }), false)
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'playing',
    sequence: 0,
  }), true)
  assert.equal(controller.acceptSpeechStatus({
    ...chatOwner,
    kind: 'terminal',
    state: 'completed',
  }), false)
  assert.equal(controller.acceptChatTerminal({
    ...chatOwner,
    operationId: 'chat_operation_wrong',
    outcome: 'completed',
  }), false)
})

test('returns detached snapshots and isolates faulty subscribers', () => {
  const controller = new VoiceSessionController()
  let observed = 0
  controller.subscribe(() => {
    observed += 1
    throw new Error('A view subscriber must not own lifecycle progress.')
  })
  controller.bind({ chatId: 'chat_detached', projectId: null })
  const snapshot = controller.getSnapshot()
  snapshot.binding.chatId = 'mutated_outside_controller'

  assert.equal(observed, 1)
  assert.equal(controller.getSnapshot().binding?.chatId, 'chat_detached')
  assert.equal(JSON.stringify(controller.getSnapshot()).toLowerCase().includes('pcm'), false)
  assert.equal(JSON.stringify(controller.getSnapshot()).toLowerCase().includes('base64'), false)
})

test('dispose rejects future actions and ignores asynchronous results', () => {
  const controller = createBoundController()
  const owner = currentOwner(controller)
  controller.startListening('voice_capture_dispose')
  controller.dispose()
  controller.dispose()

  assert.throws(
    () => controller.startListening('voice_capture_after_dispose'),
    VoiceSessionTransitionError,
  )
  assert.equal(controller.acceptCaptureComplete({
    ...owner,
    captureSessionId: 'voice_capture_dispose',
  }), false)
})
