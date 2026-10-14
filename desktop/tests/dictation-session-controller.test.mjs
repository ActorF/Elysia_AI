/** Verify bounded continuous Dictation ordering, pre-ACK races, draft safety and PCM cleanup. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DICTATION_MAXIMUM_QUEUED_PCM_BYTES,
  DictationSessionController,
} from '../src/voice/dictation-session-controller.ts'

const BINDING = { chatId: 'chat_alpha', projectId: 'project_alpha' }

/** Expose acknowledgement completion so event-before-ACK races can be scheduled explicitly. */
function deferred() {
  let resolve
  let reject
  const promise = new Promise((success, failure) => { resolve = success; reject = failure })
  return { promise, resolve, reject }
}

/** Build a valid speech segment with observable nonzero PCM ownership. */
function segment(id, sampleCount = 3_200) {
  return {
    sessionId: `voice_${id}`, sampleRate: 16_000, channelCount: 1,
    sampleFormat: 's16le', sampleCount, speechStartSample: 0,
    speechEndSample: sampleCount, pcm: new Int16Array(sampleCount).fill(17),
  }
}

/** Flush acknowledgement handlers without depending on wall-clock timers. */
async function settle() {
  await new Promise((resolve) => { setImmediate(resolve) })
}

/** Create isolated transport/persistence adapters that record exact ownership. */
function setup({ persist = () => true, onError, onFinished, start, cancel } = {}) {
  const starts = []
  const cancellations = []
  const transcripts = []
  const errors = []
  let finished = 0
  const controller = new DictationSessionController({
    startTranscription: (audio, binding) => {
      const acknowledgement = deferred()
      starts.push({ sessionId: audio.sessionId, binding, samples: audio.pcm.slice(), pcm: audio.pcm, acknowledgement })
      return start?.(audio, binding, acknowledgement) ?? acknowledgement.promise
    },
    cancelTranscription: (requestId) => {
      cancellations.push(requestId)
      return cancel?.(requestId) ?? Promise.resolve()
    },
    onTranscript: (text, binding) => {
      transcripts.push({ text, binding })
      return persist(text, binding, controller)
    },
    onError: (message) => { errors.push(message); onError?.(controller, message) },
    onFinished: () => { finished += 1; onFinished?.(controller) },
  })
  controller.begin(BINDING)
  return { controller, starts, cancellations, transcripts, errors, finished: () => finished }
}

/** Correlate final text with one dispatched test utterance. */
function finalFor(start, requestId, text = 'Spoken words.') {
  return { sessionId: start.sessionId, chatId: start.binding.chatId, requestId, text }
}

test('listens independently while exactly one queued STT request owns each acknowledgement and final', async () => {
  const { controller, starts, transcripts } = setup()
  const first = segment('first')
  const second = segment('second')
  assert.equal(controller.enqueue(first), true)
  assert.equal(controller.enqueue(second), true)
  assert.equal(starts.length, 1)
  assert.equal(controller.getSnapshot().listening, true)
  assert.equal(controller.getSnapshot().pending, true)
  assert.ok(first.pcm.every((sample) => sample === 0))
  assert.ok(second.pcm.every((sample) => sample === 17))
  assert.ok(starts[0].samples.every((sample) => sample === 17))
  starts[0].acknowledgement.resolve({ requestId: 'stt_first' })
  await settle()
  assert.equal(starts.length, 1)
  assert.equal(controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_first', 'First.')), true)
  assert.equal(starts.length, 2)
  assert.ok(second.pcm.every((sample) => sample === 0))
  starts[1].acknowledgement.resolve({ requestId: 'stt_second' })
  await settle()
  assert.equal(controller.acceptTranscriptionFinal(finalFor(starts[1], 'stt_second', 'Second.')), true)
  assert.deepEqual(transcripts, [{ text: 'First.', binding: BINDING }, { text: 'Second.', binding: BINDING }])
  assert.equal(controller.getSnapshot().pending, false)
  assert.equal(controller.getSnapshot().listening, true)
})

test('holds a pre-ACK final without persisting it or dispatching the next utterance', async () => {
  const { controller, starts, transcripts } = setup()
  controller.enqueue(segment('first'))
  controller.enqueue(segment('second'))
  const final = finalFor(starts[0], 'stt_first')
  assert.equal(controller.acceptTranscriptionFinal(final), true)
  assert.equal(controller.acceptTranscriptionFinal(final), false)
  assert.equal(starts.length, 1)
  assert.deepEqual(transcripts, [])
  assert.equal(controller.getSnapshot().pending, true)
  starts[0].acknowledgement.resolve({ requestId: 'stt_first' })
  await settle()
  assert.equal(starts.length, 2)
  assert.deepEqual(transcripts.map((item) => item.text), ['Spoken words.'])
  assert.equal(controller.acceptTranscriptionFinal(final), false)
  controller.cancel()
})

test('explicit finish waits for final flush, queued requests, and a late acknowledgement before finishing once', async () => {
  const { controller, starts, finished } = setup()
  controller.enqueue(segment('first'))
  controller.enqueue(segment('final_flush'))
  controller.finish()
  assert.deepEqual({ ...controller.getSnapshot(), epoch: undefined }, {
    epoch: undefined, listening: false, finishing: true, pending: true, error: null, binding: BINDING,
  })
  assert.equal(finished(), 0)
  controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_first'))
  assert.equal(starts.length, 1)
  starts[0].acknowledgement.resolve({ requestId: 'stt_first' })
  await settle()
  assert.equal(starts.length, 2)
  controller.acceptTranscriptionFinal(finalFor(starts[1], 'stt_flush'))
  assert.equal(finished(), 0)
  starts[1].acknowledgement.resolve({ requestId: 'stt_flush' })
  await settle()
  assert.equal(finished(), 1)
  assert.equal(controller.getSnapshot().finishing, false)
  assert.equal(controller.getSnapshot().pending, false)
  assert.equal(controller.getSnapshot().binding, null)
  controller.finish()
  assert.equal(finished(), 1)
  const late = segment('after_finish')
  assert.equal(controller.enqueue(late), false)
  assert.ok(late.pcm.every((sample) => sample === 0))
})

test('finish without speech settles immediately and cancel never invokes the finish callback', () => {
  const first = setup()
  first.controller.finish()
  assert.equal(first.finished(), 1)
  const second = setup()
  second.controller.cancel()
  assert.equal(second.finished(), 0)
  assert.equal(second.controller.getSnapshot().binding, null)
})

test('cancel before acknowledgement wipes every queue buffer and cancels exactly the old late request', async () => {
  const { controller, starts, cancellations, transcripts, finished } = setup()
  const queued = segment('queued')
  controller.enqueue(segment('first'))
  controller.enqueue(queued)
  controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_old'))
  controller.cancel()
  assert.ok(queued.pcm.every((sample) => sample === 0))
  assert.equal(controller.getSnapshot().pending, true)
  assert.deepEqual(transcripts, [])
  controller.begin({ chatId: 'chat_beta', projectId: null })
  controller.enqueue(segment('new'))
  assert.equal(starts.length, 1)
  starts[0].acknowledgement.resolve({ requestId: 'stt_old' })
  await settle()
  assert.deepEqual(cancellations, ['stt_old'])
  assert.equal(controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_old', 'Stale.')), false)
  starts[1].acknowledgement.resolve({ requestId: 'stt_new' })
  await settle()
  controller.acceptTranscriptionFinal(finalFor(starts[1], 'stt_new', 'Current.'))
  assert.deepEqual(transcripts, [{ text: 'Current.', binding: { chatId: 'chat_beta', projectId: null } }])
  assert.equal(finished(), 0)
})

test('rejects wrong session, chat and known request without modifying the active draft', async () => {
  const { controller, starts, transcripts } = setup()
  controller.enqueue(segment('first'))
  starts[0].acknowledgement.resolve({ requestId: 'stt_first' })
  await settle()
  const final = finalFor(starts[0], 'stt_first')
  for (const wrong of [{ sessionId: 'voice_wrong' }, { chatId: 'chat_wrong' }, { requestId: 'stt_wrong' }]) {
    assert.equal(controller.acceptTranscriptionFinal({ ...final, ...wrong }), false)
    assert.equal(controller.acceptTranscriptionError({ ...final, ...wrong, message: 'Stale error.' }), false)
  }
  assert.deepEqual(transcripts, [])
  assert.equal(controller.getSnapshot().listening, true)
  controller.cancel()
})

test('a mismatched pre-ACK request fails closed and cancels only the actual acknowledged request', async () => {
  const { controller, starts, errors, cancellations, transcripts } = setup()
  const queued = segment('queued')
  controller.enqueue(segment('first'))
  controller.enqueue(queued)
  controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_unconfirmed'))
  starts[0].acknowledgement.resolve({ requestId: 'stt_actual' })
  await settle()
  assert.deepEqual(transcripts, [])
  assert.deepEqual(cancellations, ['stt_actual'])
  assert.equal(errors.length, 1)
  assert.equal(controller.getSnapshot().listening, false)
  assert.ok(queued.pcm.every((sample) => sample === 0))
})

test('failure before acknowledgement stops capture immediately and cancels the later exact acknowledgement', async () => {
  const { controller, starts, errors, cancellations } = setup()
  const queued = segment('queued')
  controller.enqueue(segment('first'))
  controller.enqueue(queued)
  assert.equal(controller.acceptTranscriptionError({ ...finalFor(starts[0], 'stt_first'), message: 'Transcription unavailable.' }), true)
  assert.equal(controller.getSnapshot().listening, false)
  assert.equal(controller.getSnapshot().pending, true)
  assert.deepEqual(errors, ['Transcription unavailable.'])
  assert.ok(queued.pcm.every((sample) => sample === 0))
  starts[0].acknowledgement.resolve({ requestId: 'stt_first' })
  await settle()
  assert.deepEqual(cancellations, ['stt_first'])
  assert.equal(controller.getSnapshot().pending, false)
})

test('queue overflow reports loss risk, stops listening and wipes waiting PCM without discarding saved text', async () => {
  const { controller, starts, errors, transcripts } = setup()
  controller.enqueue(segment('already_saved'))
  starts[0].acknowledgement.resolve({ requestId: 'stt_saved' })
  await settle()
  controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_saved', 'Saved first.'))
  controller.enqueue(segment('pending', 480_000))
  const queued = []
  for (let index = 0; index < 4; index += 1) {
    queued.push(segment(`queued_${index}`, 480_000))
    assert.equal(controller.enqueue(queued[index]), true)
  }
  assert.equal(queued.reduce((sum, audio) => sum + audio.pcm.byteLength, 0), DICTATION_MAXIMUM_QUEUED_PCM_BYTES)
  const overflow = segment('overflow')
  assert.equal(controller.enqueue(overflow), false)
  assert.equal(controller.getSnapshot().listening, false)
  assert.equal(controller.getSnapshot().pending, true)
  assert.match(errors[0], /could not keep up/)
  assert.deepEqual(transcripts.map((item) => item.text), ['Saved first.'])
  assert.ok([...queued, overflow].every((audio) => audio.pcm.every((sample) => sample === 0)))
  starts[1].acknowledgement.resolve({ requestId: 'stt_pending' })
  await settle()
  assert.equal(controller.getSnapshot().pending, true)
  assert.equal(controller.acceptTranscriptionError({ ...finalFor(starts[1], 'stt_pending'), message: 'Cancelled.' }), true)
  assert.equal(controller.getSnapshot().pending, false)
})

test('cancel-before-ACK retains its target terminal barrier and never starts a replacement behind a cancellation ACK alone', async () => {
  const cancellation = deferred()
  const { controller, starts, cancellations, transcripts } = setup({ cancel: () => cancellation.promise })
  controller.enqueue(segment('old'))
  controller.cancel()
  controller.begin({ chatId: 'chat_new', projectId: null })
  const replacement = segment('replacement')
  controller.enqueue(replacement)
  assert.equal(starts.length, 1)
  starts[0].acknowledgement.resolve({ requestId: 'stt_old' })
  await settle()
  assert.deepEqual(cancellations, ['stt_old'])
  cancellation.resolve()
  await settle()
  assert.equal(starts.length, 1)
  assert.equal(controller.getSnapshot().pending, true)
  assert.ok(replacement.pcm.every((sample) => sample === 17))
  assert.equal(controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_old', 'Never append retired words.')), true)
  assert.equal(starts.length, 2)
  assert.deepEqual(transcripts, [])
  assert.ok(replacement.pcm.every((sample) => sample === 0))
})

test('retired terminal before acknowledgement releases only after its matching late ACK and cancellation settle', async () => {
  const cancellation = deferred()
  const { controller, starts, transcripts } = setup({ cancel: () => cancellation.promise })
  controller.enqueue(segment('old'))
  controller.cancel()
  assert.equal(controller.acceptTranscriptionError({ ...finalFor(starts[0], 'stt_old'), message: 'Cancelled.' }), true)
  assert.equal(controller.getSnapshot().pending, true)
  starts[0].acknowledgement.resolve({ requestId: 'stt_old' })
  await settle()
  assert.equal(controller.getSnapshot().pending, true)
  cancellation.resolve()
  await settle()
  assert.equal(controller.getSnapshot().pending, false)
  assert.deepEqual(transcripts, [])
})

test('confirmed transport loss forgets unreachable terminal barriers while late old callbacks cannot affect a restart', async () => {
  const { controller, starts, cancellations, transcripts } = setup()
  controller.enqueue(segment('old'))
  controller.cancel({ transportLost: true })
  assert.equal(controller.getSnapshot().pending, false)
  controller.begin({ chatId: 'chat_restart', projectId: null })
  controller.enqueue(segment('restart'))
  assert.equal(starts.length, 2)
  assert.equal(controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_old', 'Stale transport.')), false)
  starts[0].acknowledgement.resolve({ requestId: 'stt_old' })
  await settle()
  assert.deepEqual(cancellations, ['stt_old'])
  assert.equal(controller.getSnapshot().binding.chatId, 'chat_restart')
  starts[1].acknowledgement.resolve({ requestId: 'stt_restart' })
  await settle()
  controller.acceptTranscriptionFinal(finalFor(starts[1], 'stt_restart', 'Restarted.'))
  assert.deepEqual(transcripts, [{ text: 'Restarted.', binding: { chatId: 'chat_restart', projectId: null } }])
})

test('draft persistence failure aborts queued words and supports a reentrant microphone/controller cancellation', async () => {
  const { controller, starts, errors, finished } = setup({ persist: () => false, onError: (current) => current.cancel() })
  const queued = segment('queued')
  controller.enqueue(segment('first'))
  controller.enqueue(queued)
  controller.finish()
  starts[0].acknowledgement.resolve({ requestId: 'stt_first' })
  await settle()
  controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_first'))
  assert.match(errors[0], /could not be saved/)
  assert.equal(starts.length, 1)
  assert.equal(controller.getSnapshot().binding, null)
  assert.equal(finished(), 0)
  assert.ok(queued.pcm.every((sample) => sample === 0))
})

test('empty final text remains a successful utterance and does not stop the listening loop', async () => {
  const { controller, starts, transcripts, errors } = setup()
  controller.enqueue(segment('empty'))
  starts[0].acknowledgement.resolve({ requestId: 'stt_empty' })
  await settle()
  assert.equal(controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_empty', '')), true)
  assert.deepEqual(transcripts.map((item) => item.text), [''])
  assert.equal(controller.getSnapshot().listening, true)
  assert.equal(controller.getSnapshot().pending, false)
  assert.deepEqual(errors, [])
})

test('transport rejection wipes dispatched and queued PCM and does not expose native exception details', async () => {
  const { controller, starts, errors } = setup()
  const first = segment('first')
  const queued = segment('queued')
  controller.enqueue(first)
  controller.enqueue(queued)
  starts[0].acknowledgement.reject(new Error('Private engine path D:/secret/model.bin'))
  await settle()
  assert.equal(controller.getSnapshot().pending, false)
  assert.equal(controller.getSnapshot().listening, false)
  assert.equal(errors.length, 1)
  assert.ok(!errors[0].includes('secret'))
  assert.ok([first, queued].every((audio) => audio.pcm.every((sample) => sample === 0)))
})

test('a synchronous transport throw still wipes PCM before enqueue returns', () => {
  const { controller, errors } = setup({ start: () => { throw new Error('Native details.') } })
  const audio = segment('first')
  controller.enqueue(audio)
  assert.ok(audio.pcm.every((sample) => sample === 0))
  assert.equal(controller.getSnapshot().listening, false)
  assert.equal(errors.length, 1)
})

test('immutable snapshots and isolated failing observers cannot interrupt cancellation cleanup', () => {
  const { controller } = setup()
  const states = []
  const unsubscribe = controller.subscribe((snapshot) => states.push(snapshot))
  controller.subscribe(() => { throw new Error('Renderer observer failure.') })
  const snapshot = controller.getSnapshot()
  assert.throws(() => { snapshot.binding.chatId = 'chat_wrong' }, TypeError)
  controller.enqueue(segment('first'))
  controller.cancel()
  assert.equal(controller.getSnapshot().binding, null)
  assert.ok(states.length > 0)
  assert.ok(states.every((state) => !JSON.stringify(state).includes('pcm')))
  const count = states.length
  unsubscribe()
  unsubscribe()
  controller.begin(BINDING)
  assert.equal(states.length, count)
})

test('a callback that replaces the session cannot pump queued PCM or finish the new owner', async () => {
  const { controller, starts, finished } = setup({ persist: (_text, _binding, current) => { current.begin({ chatId: 'chat_new', projectId: null }); return true } })
  const queued = segment('old_queued')
  controller.enqueue(segment('first'))
  controller.enqueue(queued)
  controller.finish()
  starts[0].acknowledgement.resolve({ requestId: 'stt_first' })
  await settle()
  controller.acceptTranscriptionFinal(finalFor(starts[0], 'stt_first'))
  assert.equal(controller.getSnapshot().binding.chatId, 'chat_new')
  assert.equal(controller.getSnapshot().listening, true)
  assert.equal(starts.length, 1)
  assert.equal(finished(), 0)
  assert.ok(queued.pcm.every((sample) => sample === 0))
})

test('invalid segments fail closed and inactive segments are wiped without starting STT', () => {
  const { controller, starts, errors } = setup()
  const invalid = { ...segment('invalid'), sampleCount: 5 }
  assert.equal(controller.enqueue(invalid), false)
  assert.equal(starts.length, 0)
  assert.equal(errors.length, 1)
  const afterFailure = segment('after_failure')
  assert.equal(controller.enqueue(afterFailure), false)
  assert.ok(afterFailure.pcm.every((sample) => sample === 0))
  controller.cancel()
  assert.throws(() => controller.begin({ chatId: '', projectId: null }), /valid conversation/)
})
