/** Verify bounded binary speech framing and explicit delivery backpressure. */

import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { PassThrough } from 'node:stream'
import test from 'node:test'

import {
  SPEECH_AUDIO_HEADER_BYTES,
  SPEECH_AUDIO_MAX_DURATION_SECONDS,
  SPEECH_AUDIO_MAX_WAV_BYTES,
  SPEECH_AUDIO_SAMPLE_RATE_HZ,
  SpeechAudioChannelReader,
  SpeechAudioChannelStateError,
} from '../dist-electron/speech-audio-channel.js'

const TOKEN_PREFIX = Buffer.alloc(24, 0x5a)

function pcmWav({
  bitsPerSample = 16,
  channels = 1,
  sampleRate = SPEECH_AUDIO_SAMPLE_RATE_HZ,
  samples = 8,
} = {}) {
  const frameBytes = channels * bitsPerSample / 8
  const dataBytes = samples * frameBytes
  const wav = Buffer.alloc(44 + dataBytes)
  wav.write('RIFF', 0, 'ascii')
  wav.writeUInt32LE(wav.length - 8, 4)
  wav.write('WAVE', 8, 'ascii')
  wav.write('fmt ', 12, 'ascii')
  wav.writeUInt32LE(16, 16)
  wav.writeUInt16LE(1, 20)
  wav.writeUInt16LE(channels, 22)
  wav.writeUInt32LE(sampleRate, 24)
  wav.writeUInt32LE(sampleRate * frameBytes, 28)
  wav.writeUInt16LE(frameBytes, 32)
  wav.writeUInt16LE(bitsPerSample, 34)
  wav.write('data', 36, 'ascii')
  wav.writeUInt32LE(dataBytes, 40)
  return wav
}

function tokenFor(counter, prefix = TOKEN_PREFIX) {
  const token = Buffer.alloc(32)
  prefix.copy(token, 0)
  token.writeBigUInt64BE(BigInt(counter), 24)
  return token
}

function frameHeader({
  byteLength,
  digest = Buffer.alloc(32),
  format = 1,
  magic = 'ELYSAUD1',
  reserved = 0,
  sequence = 0,
  token = tokenFor(0),
  version = 1,
}) {
  const header = Buffer.alloc(SPEECH_AUDIO_HEADER_BYTES)
  header.write(magic, 0, 'ascii')
  header.writeUInt8(version, 8)
  header.writeUInt8(format, 9)
  header.writeUInt16LE(reserved, 10)
  header.writeUInt32LE(byteLength, 12)
  header.writeUInt32LE(sequence, 16)
  token.copy(header, 20)
  digest.copy(header, 52)
  return header
}

function encodedFrame({
  counter = 0,
  digest,
  prefix = TOKEN_PREFIX,
  sequence = 0,
  wav = pcmWav(),
} = {}) {
  const actualDigest = createHash('sha256').update(wav).digest()
  const token = tokenFor(counter, prefix)
  const header = frameHeader({
    byteLength: wav.length,
    digest: digest ?? actualDigest,
    sequence,
    token,
  })
  return {
    bytes: Buffer.concat([header, wav]),
    digestHex: actualDigest.toString('hex'),
    tokenHex: token.toString('hex'),
    wav,
  }
}

function immediate() {
  return new Promise((resolve) => setImmediate(resolve))
}

function assertReaderListenersRemoved(input) {
  for (const event of ['data', 'end', 'close', 'error']) {
    assert.equal(input.listenerCount(event), 0, `${event} listener leaked`)
  }
}

test('reader mirrors the fixed 84-byte little-endian envelope', () => {
  const input = new PassThrough()
  const source = encodedFrame({ counter: 9, sequence: 0x1020_3040 })
  const failures = []
  const frames = []
  const reader = new SpeechAudioChannelReader(
    input,
    (frame) => frames.push(frame),
    (failure) => failures.push(failure),
  )

  input.write(source.bytes)

  assert.equal(SPEECH_AUDIO_HEADER_BYTES, 84)
  assert.equal(frames.length, 1)
  assert.deepEqual(
    {
      byteLength: frames[0].byteLength,
      clipToken: frames[0].clipToken,
      mediaType: frames[0].mediaType,
      sequence: frames[0].sequence,
      sha256Hex: frames[0].sha256Hex,
    },
    {
      byteLength: source.wav.length,
      clipToken: source.tokenHex,
      mediaType: 'audio/wav',
      sequence: 0x1020_3040,
      sha256Hex: source.digestHex,
    },
  )
  assert.deepEqual(frames[0].wavBytes, source.wav)
  assert.ok(Object.isFrozen(frames[0]))
  assert.equal(input.isPaused(), true)
  assert.deepEqual(failures, [])

  reader.acknowledge(source.tokenHex)
  reader.dispose()
  input.destroy()
})

test('reader accepts a frame split at every byte boundary', () => {
  const source = encodedFrame({ sequence: 17 })

  for (let split = 1; split < source.bytes.length; split += 1) {
    const input = new PassThrough()
    const frames = []
    const failures = []
    const reader = new SpeechAudioChannelReader(
      input,
      (frame) => frames.push(frame),
      (failure) => failures.push(failure),
    )

    input.write(source.bytes.subarray(0, split))
    assert.equal(frames.length, 0, `delivered before split ${split}`)
    input.write(source.bytes.subarray(split))
    assert.equal(frames.length, 1, `failed at split ${split}`)
    assert.deepEqual(frames[0].wavBytes, source.wav)
    assert.deepEqual(failures, [])

    reader.acknowledge(source.tokenHex)
    reader.dispose()
    input.destroy()
  }
})

test('reader accepts a frame fragmented one byte at a time', () => {
  const input = new PassThrough()
  const source = encodedFrame({ sequence: 3 })
  const frames = []
  const failures = []
  const reader = new SpeechAudioChannelReader(
    input,
    (frame) => frames.push(frame),
    (failure) => failures.push(failure),
  )

  for (const byte of source.bytes) {
    input.write(Buffer.from([byte]))
  }

  assert.equal(frames.length, 1)
  assert.deepEqual(frames[0].wavBytes, source.wav)
  assert.deepEqual(failures, [])
  reader.discard(source.tokenHex)
  reader.dispose()
  input.destroy()
})

test('coalesced frames remain ordered behind one explicit acknowledgement', async () => {
  const input = new PassThrough()
  const first = encodedFrame({ counter: 0, sequence: 10 })
  const second = encodedFrame({ counter: 2, sequence: 11 })
  const frames = []
  const failures = []
  let resolveSecond
  const secondDelivered = new Promise((resolve) => {
    resolveSecond = resolve
  })
  const reader = new SpeechAudioChannelReader(
    input,
    (frame) => {
      frames.push(frame)
      if (frames.length === 2) {
        resolveSecond()
      }
    },
    (failure) => failures.push(failure),
  )

  input.write(Buffer.concat([first.bytes, second.bytes]))
  assert.deepEqual(frames.map((frame) => frame.sequence), [10])
  assert.equal(input.isPaused(), true)

  reader.acknowledge(first.tokenHex)
  await secondDelivered
  assert.deepEqual(frames.map((frame) => frame.sequence), [10, 11])
  assert.equal(input.isPaused(), true)
  assert.deepEqual(failures, [])

  reader.discard(second.tokenHex)
  reader.dispose()
  input.destroy()
})

test('pause applies backpressure until a delivered token is released', async () => {
  const input = new PassThrough({ highWaterMark: 64 })
  const first = encodedFrame({ counter: 0, sequence: 0 })
  const second = encodedFrame({ counter: 1, sequence: 1 })
  const frames = []
  const reader = new SpeechAudioChannelReader(
    input,
    (frame) => frames.push(frame),
    (failure) => assert.fail(`unexpected failure: ${failure}`),
  )

  input.write(first.bytes)
  assert.equal(input.isPaused(), true)
  const acceptedWithoutBackpressure = input.write(second.bytes)
  assert.equal(acceptedWithoutBackpressure, false)
  await immediate()
  assert.deepEqual(frames.map((frame) => frame.sequence), [0])

  reader.acknowledge(first.tokenHex)
  await immediate()
  assert.deepEqual(frames.map((frame) => frame.sequence), [0, 1])
  assert.equal(input.isPaused(), true)

  reader.acknowledge(second.tokenHex)
  reader.dispose()
  input.destroy()
})

for (const [name, bytes] of [
  ['partial header', frameHeader({ byteLength: 46 }).subarray(0, 83)],
  ['partial payload', encodedFrame().bytes.subarray(0, -1)],
]) {
  test(`EOF rejects a ${name} as truncated`, async () => {
    const input = new PassThrough()
    const failures = []
    const failed = new Promise((resolve) => {
      new SpeechAudioChannelReader(
        input,
        () => assert.fail('truncated input must not be delivered'),
        (failure) => {
          failures.push(failure)
          resolve()
        },
      )
    })

    input.end(bytes)
    await failed

    assert.deepEqual(failures, ['truncated-frame'])
    assertReaderListenersRemoved(input)
    input.destroy()
  })
}

test('clean EOF detaches the reader without reporting a failure', async () => {
  const input = new PassThrough()
  const failures = []
  new SpeechAudioChannelReader(
    input,
    () => assert.fail('empty input must not deliver a frame'),
    (failure) => failures.push(failure),
  )
  const ended = new Promise((resolve) => input.once('end', resolve))

  input.end()
  await ended

  assert.deepEqual(failures, [])
  assertReaderListenersRemoved(input)
})

test('EOF after a complete frame waits for its final acknowledgement', async () => {
  const input = new PassThrough()
  const source = encodedFrame()
  const frames = []
  const failures = []
  const reader = new SpeechAudioChannelReader(
    input,
    (frame) => frames.push(frame),
    (failure) => failures.push(failure),
  )

  input.end(source.bytes)
  await immediate()

  assert.equal(frames.length, 1)
  assert.deepEqual(failures, [])
  assertReaderListenersRemoved(input)
  reader.acknowledge(source.tokenHex)
  assert.deepEqual(failures, [])
})

test('extra bytes after a complete frame are parsed only after acknowledgement', async () => {
  const input = new PassThrough()
  const source = encodedFrame()
  const frames = []
  const failures = []
  const failed = new Promise((resolve) => {
    const reader = new SpeechAudioChannelReader(
      input,
      (frame) => {
        frames.push(frame)
        reader.acknowledge(frame.clipToken)
      },
      (failure) => {
        failures.push(failure)
        resolve()
      },
    )
  })

  input.end(Buffer.concat([source.bytes, Buffer.from([0x45])]))
  await failed

  assert.equal(frames.length, 1)
  assert.deepEqual(failures, ['truncated-frame'])
  assertReaderListenersRemoved(input)
})

for (const [name, byteLength, expectedFailure] of [
  ['zero length', 0, 'invalid-header'],
  ['undersized WAV', 45, 'invalid-header'],
  ['oversized WAV', SPEECH_AUDIO_MAX_WAV_BYTES + 1, 'frame-too-large'],
]) {
  test(`header rejects ${name} before waiting for payload`, () => {
    const input = new PassThrough()
    const failures = []
    new SpeechAudioChannelReader(
      input,
      () => assert.fail('invalid length must not deliver a frame'),
      (failure) => failures.push(failure),
    )

    input.write(frameHeader({ byteLength }))

    assert.deepEqual(failures, [expectedFailure])
    assertReaderListenersRemoved(input)
    assert.equal(input.destroyed, false)
    input.destroy()
  })
}

for (const [name, mutate] of [
  ['magic', (header) => header.write('BADMAGIC', 0, 'ascii')],
  ['version', (header) => header.writeUInt8(2, 8)],
  ['format', (header) => header.writeUInt8(2, 9)],
  ['reserved bits', (header) => header.writeUInt16LE(1, 10)],
]) {
  test(`header rejects bad ${name}`, () => {
    const input = new PassThrough()
    const source = encodedFrame()
    const bytes = Buffer.from(source.bytes)
    mutate(bytes.subarray(0, SPEECH_AUDIO_HEADER_BYTES))
    const failures = []
    new SpeechAudioChannelReader(
      input,
      () => assert.fail('bad header must not deliver a frame'),
      (failure) => failures.push(failure),
    )

    input.write(bytes)

    assert.deepEqual(failures, ['invalid-header'])
    input.destroy()
  })
}

test('reader rejects a payload whose incremental digest mismatches the header', () => {
  const input = new PassThrough()
  const source = encodedFrame({ digest: Buffer.alloc(32, 0xff) })
  const failures = []
  new SpeechAudioChannelReader(
    input,
    () => assert.fail('bad digest must not deliver a frame'),
    (failure) => failures.push(failure),
  )

  input.write(source.bytes)

  assert.deepEqual(failures, ['hash-mismatch'])
  assertReaderListenersRemoved(input)
  input.destroy()
})

for (const [name, makeWav, expectedFailure = 'invalid-wav'] of [
  ['wrong RIFF magic', () => {
    const wav = pcmWav()
    wav.write('RIFX', 0, 'ascii')
    return wav
  }],
  ['wrong RIFF length', () => {
    const wav = pcmWav()
    wav.writeUInt32LE(wav.length - 9, 4)
    return wav
  }],
  ['noncanonical sample rate', () => pcmWav({ sampleRate: 16_000 })],
  ['stereo PCM', () => pcmWav({ channels: 2 })],
  ['8-bit PCM', () => pcmWav({ bitsPerSample: 8 })],
  ['empty data chunk', () => pcmWav({ samples: 0 }), 'invalid-header'],
  ['extra trailing chunk', () => {
    const wav = Buffer.concat([pcmWav(), Buffer.from('JUNK')])
    wav.writeUInt32LE(wav.length - 8, 4)
    return wav
  }],
]) {
  test(`reader rejects ${name}`, () => {
    const input = new PassThrough()
    const source = encodedFrame({ wav: makeWav() })
    const failures = []
    new SpeechAudioChannelReader(
      input,
      () => assert.fail('noncanonical WAV must not be delivered'),
      (failure) => failures.push(failure),
    )

    input.write(source.bytes)

    assert.deepEqual(failures, [expectedFailure])
    input.destroy()
  })
}

test('reader admits exactly 120 seconds and rejects one additional sample', () => {
  const acceptedInput = new PassThrough()
  const maximum = encodedFrame({
    wav: pcmWav({
      samples: SPEECH_AUDIO_SAMPLE_RATE_HZ * SPEECH_AUDIO_MAX_DURATION_SECONDS,
    }),
  })
  const accepted = []
  const acceptedReader = new SpeechAudioChannelReader(
    acceptedInput,
    (frame) => accepted.push(frame),
    (failure) => assert.fail(`unexpected maximum failure: ${failure}`),
  )
  acceptedInput.write(maximum.bytes)
  assert.equal(accepted.length, 1)
  acceptedReader.acknowledge(maximum.tokenHex)
  acceptedReader.dispose()
  acceptedInput.destroy()

  const rejectedInput = new PassThrough()
  const overDuration = encodedFrame({
    wav: pcmWav({
      samples: (
        SPEECH_AUDIO_SAMPLE_RATE_HZ
        * SPEECH_AUDIO_MAX_DURATION_SECONDS
        + 1
      ),
    }),
  })
  const failures = []
  new SpeechAudioChannelReader(
    rejectedInput,
    () => assert.fail('overlong WAV must not be delivered'),
    (failure) => failures.push(failure),
  )
  rejectedInput.write(overDuration.bytes)
  assert.deepEqual(failures, ['invalid-wav'])
  rejectedInput.destroy()
})

test('reader rejects a duplicate token counter after acknowledgement', async () => {
  const input = new PassThrough()
  const first = encodedFrame({ counter: 7, sequence: 0 })
  const replay = encodedFrame({ counter: 7, sequence: 1 })
  const frames = []
  const failures = []
  const reader = new SpeechAudioChannelReader(
    input,
    (frame) => frames.push(frame),
    (failure) => failures.push(failure),
  )

  input.write(first.bytes)
  reader.acknowledge(first.tokenHex)
  input.write(replay.bytes)
  await immediate()

  assert.equal(frames.length, 1)
  assert.deepEqual(failures, ['invalid-token'])
  input.destroy()
})

test('reader rejects a token prefix change on one writer channel', async () => {
  const input = new PassThrough()
  const first = encodedFrame({ counter: 0 })
  const mixed = encodedFrame({
    counter: 1,
    prefix: Buffer.alloc(24, 0x6b),
  })
  const failures = []
  const reader = new SpeechAudioChannelReader(
    input,
    () => {},
    (failure) => failures.push(failure),
  )

  input.write(first.bytes)
  reader.acknowledge(first.tokenHex)
  input.write(mixed.bytes)
  await immediate()

  assert.deepEqual(failures, ['invalid-token'])
  input.destroy()
})

test('wrong acknowledgement is terminal and never exposes its token', () => {
  const input = new PassThrough()
  const source = encodedFrame()
  const failures = []
  const reader = new SpeechAudioChannelReader(
    input,
    () => {},
    (failure) => failures.push(failure),
  )
  input.write(source.bytes)
  const wrongToken = 'f'.repeat(64)

  assert.throws(
    () => reader.acknowledge(wrongToken),
    (error) => (
      error instanceof SpeechAudioChannelStateError
      && !error.message.includes(wrongToken)
      && !error.message.includes(source.tokenHex)
    ),
  )

  assert.deepEqual(failures, ['invalid-acknowledgement'])
  assertReaderListenersRemoved(input)
  assert.equal(input.destroyed, false)
  input.destroy()
})

test('duplicate acknowledgement terminates instead of releasing later bytes', () => {
  const input = new PassThrough()
  const source = encodedFrame()
  const failures = []
  const reader = new SpeechAudioChannelReader(
    input,
    () => {},
    (failure) => failures.push(failure),
  )
  input.write(source.bytes)

  reader.acknowledge(source.tokenHex)
  assert.throws(
    () => reader.acknowledge(source.tokenHex),
    SpeechAudioChannelStateError,
  )

  assert.deepEqual(failures, ['invalid-acknowledgement'])
  assertReaderListenersRemoved(input)
  input.destroy()
})

test('malformed or uppercase acknowledgement is rejected exactly', () => {
  const input = new PassThrough()
  const source = encodedFrame()
  const failures = []
  const reader = new SpeechAudioChannelReader(
    input,
    () => {},
    (failure) => failures.push(failure),
  )
  input.write(source.bytes)

  assert.throws(
    () => reader.discard(source.tokenHex.toUpperCase()),
    SpeechAudioChannelStateError,
  )
  assert.deepEqual(failures, ['invalid-acknowledgement'])
  input.destroy()
})

test('non-Buffer chunks fail closed without reflecting their content', () => {
  const input = new PassThrough()
  const secret = 'D:/private/speech.wav'
  const failures = []
  new SpeechAudioChannelReader(
    input,
    () => assert.fail('non-binary chunks must not be delivered'),
    (failure) => failures.push(failure),
  )

  input.emit('data', secret)

  assert.deepEqual(failures, ['non-binary-chunk'])
  assert.ok(!failures.join('').includes(secret))
  assertReaderListenersRemoved(input)
  input.destroy()
})

test('consumer exceptions are reduced to one sanitized terminal code', () => {
  const input = new PassThrough()
  const source = encodedFrame()
  const failures = []
  new SpeechAudioChannelReader(
    input,
    () => {
      throw new Error('D:/private/player detail')
    },
    (failure) => failures.push(failure),
  )

  assert.doesNotThrow(() => input.write(source.bytes))

  assert.deepEqual(failures, ['consumer-failed'])
  assertReaderListenersRemoved(input)
  input.destroy()
})

test('native stream errors are reduced to one sanitized terminal code', () => {
  const input = new PassThrough()
  const failures = []
  const secret = 'D:/private/native-pipe-detail'
  new SpeechAudioChannelReader(
    input,
    () => assert.fail('failed stream must not deliver a frame'),
    (failure) => failures.push(failure),
  )

  assert.doesNotThrow(() => input.emit('error', new Error(secret)))

  assert.deepEqual(failures, ['stream-failed'])
  assert.ok(!failures.join('').includes(secret))
  assertReaderListenersRemoved(input)
  input.destroy()
})

test('dispose removes only reader listeners and does not destroy its stream', () => {
  const input = new PassThrough()
  const ownerListener = () => {}
  input.on('error', ownerListener)
  const reader = new SpeechAudioChannelReader(
    input,
    () => {},
    () => {},
  )

  reader.dispose()
  reader.dispose()

  assert.equal(input.listenerCount('data'), 0)
  assert.equal(input.listenerCount('end'), 0)
  assert.equal(input.listenerCount('close'), 0)
  assert.equal(input.listenerCount('error'), 1)
  assert.equal(input.listeners('error')[0], ownerListener)
  assert.equal(input.destroyed, false)
  assert.equal(input.isPaused(), true)
  input.off('error', ownerListener)
  input.destroy()
})
