/** Verify the Electron bridge and Python backend share one strict wire contract. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { PassThrough } from 'node:stream'
import test from 'node:test'
import { pathToFileURL } from 'node:url'

import { BackendProcess } from '../dist-electron/backend-process.js'
import { BoundedNdjsonReader } from '../dist-electron/bounded-ndjson.js'
import {
  allowAudioPermissionCheck,
  allowAudioPermissionRequest,
} from '../dist-electron/audio-permission.js'
import { parseSafeExternalUrl } from '../dist-electron/external-url.js'
import { isTrustedRendererUrl } from '../dist-electron/renderer-source.js'
import {
  PROTOCOL_NAME,
  PROTOCOL_VERSION,
  MAX_PROTOCOL_FRAME_BYTES,
  VOICE_SPEECH_MAX_SEQUENCE,
  VOICE_SPEECH_MAX_WAV_BYTES,
  VOICE_SPEECH_MIN_WAV_BYTES,
  VOICE_CAPTURE_MAX_BASE64_CHARACTERS,
  VOICE_TRANSCRIPTION_MAX_TEXT_CODE_POINTS,
  ProtocolValidationError,
  createRequest,
  hasNonBlankCodePoint,
  parseAttachmentStateResult,
  parseChatResult,
  parseClientRequest,
  parseHandshakeResult,
  parseInitializeResult,
  parseChatStateResult,
  parseProjectStateResult,
  parseSettingsStateResult,
  parseServerMessage,
  parseVoiceCaptureCompleteParams,
  parseVoiceCaptureResult,
  parseVoiceSettingsStateResult,
  parseVoiceTranscriptionResult,
  parseVoiceTranscriptionStartParams,
  trimProtocolBlankCharacters,
} from '../dist-electron/protocol.js'

const fixturePath = new URL(
  '../../desktop_protocol/fixtures/v1.samples.json',
  import.meta.url,
)
const fixtures = JSON.parse(await readFile(fixturePath, 'utf8'))
const schemaPath = new URL(
  '../../desktop_protocol/schema/v1.schema.json',
  import.meta.url,
)
const schema = JSON.parse(await readFile(schemaPath, 'utf8'))

test('TypeScript uses the frame limit declared by the JSON Schema', () => {
  assert.equal(
    MAX_PROTOCOL_FRAME_BYTES,
    schema['x-elysia-frameMaxBytes'],
  )
})

test('bounded NDJSON reader accepts fragmented UTF-8 and CRLF', async () => {
  const input = new PassThrough()
  const lines = []
  const failures = []
  const encoded = Buffer.from('é\r\nok\n', 'utf8')
  new BoundedNdjsonReader(
    input,
    4,
    (line) => lines.push(line),
    (failure) => failures.push(failure),
  )
  const ended = new Promise((resolve) => input.once('end', resolve))

  input.write(encoded.subarray(0, 1))
  input.write(encoded.subarray(1, 3))
  input.end(encoded.subarray(3))
  await ended

  assert.deepEqual(lines, ['é', 'ok'])
  assert.deepEqual(failures, [])
  for (const event of ['data', 'end', 'close', 'error']) {
    assert.equal(input.listenerCount(event), 0)
  }
})

test('bounded NDJSON reader accepts the exact byte limit with CRLF', async () => {
  const input = new PassThrough()
  const lines = []
  const failures = []
  new BoundedNdjsonReader(
    input,
    4,
    (line) => lines.push(line),
    (failure) => failures.push(failure),
  )
  const ended = new Promise((resolve) => input.once('end', resolve))

  input.end(Buffer.from('1234\r\n'))
  await ended

  assert.deepEqual(lines, ['1234'])
  assert.deepEqual(failures, [])
})

test('bounded NDJSON reader rejects an oversized unterminated frame immediately', () => {
  const input = new PassThrough()
  const failures = []
  new BoundedNdjsonReader(
    input,
    4,
    () => assert.fail('An oversized frame must not be delivered.'),
    (failure) => failures.push(failure),
  )

  input.write(Buffer.from('12345'))

  assert.deepEqual(failures, ['frame-too-large'])
  for (const event of ['data', 'end', 'close', 'error']) {
    assert.equal(input.listenerCount(event), 0)
  }
  input.destroy()
})

test('bounded NDJSON reader rejects malformed UTF-8 without replacement', () => {
  const input = new PassThrough()
  const failures = []
  new BoundedNdjsonReader(
    input,
    4,
    () => assert.fail('Malformed UTF-8 must not be delivered.'),
    (failure) => failures.push(failure),
  )

  input.write(Buffer.from([0xC3, 0x28, 0x0A]))

  assert.deepEqual(failures, ['invalid-utf8'])
  input.destroy()
})

test('bounded NDJSON reader rejects a final frame without newline', async () => {
  const input = new PassThrough()
  const failures = []
  new BoundedNdjsonReader(
    input,
    16,
    () => assert.fail('A truncated frame must not be delivered.'),
    (failure) => failures.push(failure),
  )
  const ended = new Promise((resolve) => input.once('end', resolve))

  input.end(Buffer.from('{"ok":true}'))
  await ended

  assert.deepEqual(failures, ['truncated-frame'])
})

test('JSON Schema declares the exact public settings surface', () => {
  assert.deepEqual(
    Object.keys(schema.$defs.settingsValues.properties),
    [
      'modelName',
      'ollamaHost',
      'shortTermMemoryTokenBudget',
      'memoryRetrievalLimit',
      'dataImportMaxBytes',
      'transcriptionModel',
      'transcriptionDevice',
      'transcriptionLanguage',
    ],
  )
  assert.equal(schema.$defs.settingsValues.additionalProperties, false)
  assert.ok(schema.$defs.settingsGetRequest)
  assert.ok(schema.$defs.settingsUpdateRequest)
  assert.ok(schema.$defs.settingsStateResult)
})

test('JSON Schema declares bounded frame-aligned Voice capture', () => {
  assert.ok(schema.$defs.voiceCaptureCompleteRequest)
  assert.ok(schema.$defs.voiceCaptureCompleteParams)
  assert.ok(schema.$defs.voiceCaptureResult)
  assert.equal(
    schema.$defs.voiceCaptureCompleteParams.properties.sampleCount.multipleOf,
    320,
  )
  assert.ok(schema['x-elysia-runtimeInvariants'].some(
    (invariant) => invariant.includes('decoded byte length equals sampleCount * 2'),
  ))
  assert.ok(schema['x-elysia-runtimeInvariants'].some(
    (invariant) => invariant.includes('aligned to 320-sample frames'),
  ))
})

test('JSON Schema declares the transcription runtime invariants', () => {
  const invariants = schema['x-elysia-runtimeInvariants']
  const status = schema.$defs.transcriptionStatus

  assert.equal(
    schema.$defs.voiceTranscriptionResult.properties.text.maxLength,
    VOICE_TRANSCRIPTION_MAX_TEXT_CODE_POINTS,
  )
  assert.ok(invariants.some(
    (invariant) => invariant.includes(
      'voice.transcription.start sampleCount and speech markers',
    ),
  ))
  assert.ok(invariants.some(
    (invariant) => invariant.includes(
      'voice.transcription.start pcmBase64 is strict canonical Base64',
    ),
  ))
  assert.ok(invariants.some(
    (invariant) => invariant.includes(
      'voice.transcription result text follows the protocol non-blank',
    ),
  ))
  assert.deepEqual(
    Object.keys(status.properties),
    [
      'state',
      'model',
      'requestedDevice',
      'resolvedDevice',
      'computeType',
      'reason',
    ],
  )
  assert.equal(status.additionalProperties, false)
  assert.deepEqual(
    status.properties.model.enum,
    ['tiny', 'base', 'small', 'medium', 'large-v3', 'turbo'],
  )
})

test('JSON Schema and TypeScript share bounded speech clip metadata', () => {
  const clipBranch = schema.$defs.event.oneOf.find(
    (branch) => branch.properties.event.const === 'voice.speech.clip',
  )
  assert.ok(clipBranch)
  assert.equal(
    clipBranch.properties.data.properties.sequence.maximum,
    VOICE_SPEECH_MAX_SEQUENCE,
  )
  assert.equal(
    clipBranch.properties.data.properties.byteLength.minimum,
    VOICE_SPEECH_MIN_WAV_BYTES,
  )
  assert.equal(
    clipBranch.properties.data.properties.byteLength.maximum,
    VOICE_SPEECH_MAX_WAV_BYTES,
  )
  assert.ok(schema['x-elysia-runtimeInvariants'].some(
    (invariant) => invariant.includes(
      'voice.speech.clip metadata exactly matches the next private binary frame',
    ),
  ))
})

for (const sample of fixtures.validClientMessages) {
  test(`TypeScript accepts client sample: ${sample.name}`, () => {
    const parsed = parseClientRequest(sample.message)
    assert.deepEqual(parsed.protocol, fixtures.protocol)
  })
}

for (const sample of fixtures.validServerMessages) {
  test(`TypeScript accepts server sample: ${sample.name}`, () => {
    const parsed = parseServerMessage(sample.message)
    assert.deepEqual(parsed.protocol, fixtures.protocol)

    if (sample.name === 'handshake response') {
      assert.equal(parsed.ok, true)
      assert.equal(
        parseHandshakeResult(parsed.result).protocol.version,
        PROTOCOL_VERSION,
      )
    }
    if (sample.name === 'initialize response') {
      assert.equal(parsed.ok, true)
      assert.equal(
        parseInitializeResult(parsed.result).chatId,
        'chat_fixture',
      )
    }
    if (sample.name === 'chat response') {
      assert.equal(parsed.ok, true)
      assert.equal(parseChatResult(parsed.result).chatId, 'chat_fixture')
    }
    if (sample.name === 'chat state response') {
      assert.equal(parsed.ok, true)
      const result = parseChatStateResult(parsed.result)
      assert.equal(result.activeChat.chatId, 'chat_fixture')
      assert.equal(result.activeChat.messages.length, 2)
      assert.deepEqual(
        result.chats.map((chat) => chat.chatId),
        ['chat_fixture', 'chat_second'],
      )
    }
    if (sample.name === 'project state response') {
      assert.equal(parsed.ok, true)
      const result = parseProjectStateResult(parsed.result)
      assert.equal(result.activeProject.projectId, 'project_fixture')
      assert.equal(result.projects.length, 2)
      assert.equal(result.projects[0].chatCount, 1)
      assert.equal(result.chatState.activeChat.chatId, 'chat_fixture')
    }
    if (sample.name === 'settings state response') {
      assert.equal(parsed.ok, true)
      const result = parseSettingsStateResult(parsed.result)
      assert.equal(result.revision, 3)
      assert.equal(result.restartRequired, true)
      assert.deepEqual(result.restartFields, [
        'modelName',
        'ollamaHost',
        'shortTermMemoryTokenBudget',
        'memoryRetrievalLimit',
        'dataImportMaxBytes',
        'transcriptionModel',
        'transcriptionDevice',
        'transcriptionLanguage',
      ])
      assert.equal(result.scopes.project.projectId, 'project_fixture')
      assert.equal(result.scopes.project.modelName, null)
      assert.equal(result.scopes.chat.chatId, 'chat_fixture')
    }
    if (sample.name === 'settings recovered defaults response') {
      assert.equal(parsed.ok, true)
      const result = parseSettingsStateResult(parsed.result)
      assert.equal(result.revision, 0)
      assert.equal(result.updatedAt, null)
      assert.equal(result.restartRequired, false)
      assert.deepEqual(result.restartFields, [])
      assert.deepEqual(result.scopes, { project: null, chat: null })
      assert.match(result.warning, /Recovered safe defaults/)
    }
  })
}

for (const sample of fixtures.invalidClientMessages) {
  test(`TypeScript rejects client sample: ${sample.name}`, () => {
    assert.throws(
      () => parseClientRequest(sample.message),
      ProtocolValidationError,
    )
  })
}

for (const sample of fixtures.invalidServerMessages) {
  test(`TypeScript rejects server sample: ${sample.name}`, () => {
    assert.throws(
      () => parseServerMessage(sample.message),
      ProtocolValidationError,
    )
  })
}

test('TypeScript request builder produces the versioned envelope', () => {
  assert.deepEqual(
    createRequest('shutdown-1', 'shutdown', {}),
    {
      type: 'request',
      protocol: {
        name: PROTOCOL_NAME,
        version: PROTOCOL_VERSION,
      },
      id: 'shutdown-1',
      method: 'shutdown',
      params: {},
    },
  )
})

test('TypeScript validates revisioned settings requests without secrets', () => {
  assert.deepEqual(
    createRequest('settings-get-1', 'settings.get', {}).params,
    {},
  )
  const settings = {
    modelName: 'qwen3.5:9b',
    ollamaHost: 'http://127.0.0.1:11434',
    shortTermMemoryTokenBudget: 2048,
    memoryRetrievalLimit: 5,
    dataImportMaxBytes: 16777216,
    transcriptionModel: 'small',
    transcriptionDevice: 'auto',
    transcriptionLanguage: 'auto',
  }
  assert.deepEqual(
    createRequest('settings-update-1', 'settings.update', {
      expectedRevision: 0,
      settings,
    }).params,
    { expectedRevision: 0, settings },
  )

  const secret = 'never-echo-this-secret'
  assert.throws(
    () => parseClientRequest({
      type: 'request',
      protocol: fixtures.protocol,
      id: 'settings-update-secret',
      method: 'settings.update',
      params: {
        expectedRevision: 0,
        settings: { ...settings, apiKey: secret },
      },
    }),
    (error) => (
      error instanceof ProtocolValidationError
      && !error.message.includes(secret)
    ),
  )

  for (const [field, value] of [
    ['transcriptionModel', 'D:/private/model'],
    ['transcriptionDevice', 'directml'],
    ['transcriptionLanguage', 'fr'],
  ]) {
    assert.throws(
      () => parseClientRequest({
        type: 'request',
        protocol: fixtures.protocol,
        id: `settings-update-invalid-${field}`,
        method: 'settings.update',
        params: {
          expectedRevision: 0,
          settings: { ...settings, [field]: value },
        },
      }),
      ProtocolValidationError,
    )
  }

})

test('TypeScript validates regenerate and edit-and-retry requests', () => {
  assert.deepEqual(
    createRequest('retry-1', 'chat.retry', {
      chatId: 'chat_fixture',
      userMessageId: 'message_user',
      assistantMessageId: 'message_assistant',
    }).params,
    {
      chatId: 'chat_fixture',
      userMessageId: 'message_user',
      assistantMessageId: 'message_assistant',
    },
  )
  assert.equal(
    createRequest('retry-2', 'chat.retry', {
      chatId: 'chat_fixture',
      userMessageId: 'message_user',
      assistantMessageId: 'message_assistant',
      message: 'Edited prompt',
    }).params.message,
    'Edited prompt',
  )
  assert.throws(
    () => parseClientRequest({
      type: 'request',
      protocol: fixtures.protocol,
      id: 'retry-invalid',
      method: 'chat.retry',
      params: {
        chatId: 'chat_fixture',
        userMessageId: 'message_user',
        assistantMessageId: 'message_assistant',
        message: '\ufeff\u0085',
      },
    }),
    ProtocolValidationError,
  )
})

test('TypeScript validates exact scoped Attachment requests', () => {
  const chatScope = { kind: 'chat', id: 'chat_fixture' }
  const projectScope = { kind: 'project', id: 'project_fixture' }
  assert.deepEqual(
    createRequest('attachment-list-1', 'attachment.list', {
      scope: chatScope,
    }).params,
    { scope: chatScope },
  )
  assert.deepEqual(
    createRequest('attachment-add-1', 'attachment.add', {
      scope: projectScope,
      sourcePaths: [String.raw`C:\Users\Actor\Documents\notes.txt`],
    }).params,
    {
      scope: projectScope,
      sourcePaths: [String.raw`C:\Users\Actor\Documents\notes.txt`],
    },
  )
  assert.deepEqual(
    createRequest('attachment-remove-1', 'attachment.remove', {
      scope: chatScope,
      attachmentId: 'attachment_fixture',
    }).params,
    { scope: chatScope, attachmentId: 'attachment_fixture' },
  )
  assert.deepEqual(
    createRequest('chat-attachments-1', 'chat.stream', {
      chatId: 'chat_fixture',
      message: 'Use the attached notes.',
      attachmentIds: ['attachment_fixture'],
    }).params.attachmentIds,
    ['attachment_fixture'],
  )
})

for (const [name, method, params] of [
  [
    'unknown scope field',
    'attachment.list',
    { scope: { kind: 'chat', id: 'chat_fixture', sourcePath: 'private' } },
  ],
  [
    'scope kind and id mismatch',
    'attachment.list',
    { scope: { kind: 'project', id: 'chat_fixture' } },
  ],
  [
    'relative source path',
    'attachment.add',
    { scope: { kind: 'chat', id: 'chat_fixture' }, sourcePaths: ['note.txt'] },
  ],
  [
    'device source path',
    'attachment.add',
    {
      scope: { kind: 'chat', id: 'chat_fixture' },
      sourcePaths: [String.raw`\\.\C:\notes.txt`],
    },
  ],
  [
    'dot-segment source path',
    'attachment.add',
    {
      scope: { kind: 'chat', id: 'chat_fixture' },
      sourcePaths: [String.raw`C:\safe\..\notes.txt`],
    },
  ],
  [
    'duplicate source path',
    'attachment.add',
    {
      scope: { kind: 'chat', id: 'chat_fixture' },
      sourcePaths: [String.raw`C:\notes.txt`, String.raw`C:\notes.txt`],
    },
  ],
  [
    'Windows-equivalent duplicate source path',
    'attachment.add',
    {
      scope: { kind: 'chat', id: 'chat_fixture' },
      sourcePaths: [String.raw`C:\Notes.txt`, 'c:/notes.txt'],
    },
  ],
  [
    'unknown remove field',
    'attachment.remove',
    {
      scope: { kind: 'chat', id: 'chat_fixture' },
      attachmentId: 'attachment_fixture',
      sourcePath: String.raw`C:\private.txt`,
    },
  ],
]) {
  test(`TypeScript rejects Attachment request with ${name}`, () => {
    assert.throws(
      () => parseClientRequest({
        type: 'request',
        protocol: fixtures.protocol,
        id: `invalid-attachment-${name}`,
        method,
        params,
      }),
      ProtocolValidationError,
    )
  })
}

test('TypeScript parses bounded Attachment state without local paths', () => {
  const state = {
    scope: { kind: 'chat', id: 'chat_fixture' },
    attachments: [
      {
        attachmentId: 'attachment_fixture',
        fileName: 'notes.txt',
        mediaType: 'text/plain',
        sizeBytes: 12,
        status: 'ready',
      },
    ],
    maxFileBytes: 16_777_216,
    maxFileCount: 10,
  }
  assert.deepEqual(parseAttachmentStateResult(state), state)

  assert.throws(
    () => parseAttachmentStateResult({
      ...state,
      attachments: [{ ...state.attachments[0], sourcePath: 'private' }],
    }),
    ProtocolValidationError,
  )
  assert.throws(
    () => parseAttachmentStateResult({
      ...state,
      attachments: [{ ...state.attachments[0], status: 'claimed' }],
    }),
    ProtocolValidationError,
  )
  assert.throws(
    () => parseAttachmentStateResult({
      ...state,
      attachments: [
        ...state.attachments,
        ...Array.from({ length: 10 }, (_, index) => ({
          ...state.attachments[0],
          attachmentId: `attachment_extra_${index}`,
        })),
      ],
    }),
    ProtocolValidationError,
  )
  for (const invalidItem of [
    { ...state.attachments[0], fileName: 'C:/secret.txt' },
    { ...state.attachments[0], mediaType: 'not a mime' },
  ]) {
    assert.throws(
      () => parseAttachmentStateResult({
        ...state,
        attachments: [invalidItem],
      }),
      ProtocolValidationError,
    )
  }

  const historicalState = {
    ...state,
    attachments: [{ ...state.attachments[0], sizeBytes: 100 }],
    maxFileBytes: 10,
  }
  assert.deepEqual(parseAttachmentStateResult(historicalState), historicalState)
})

test('renderer text helpers follow the protocol blank definition', () => {
  assert.equal(hasNonBlankCodePoint('\u0085\ufeff'), false)
  assert.equal(hasNonBlankCodePoint('\u0085hello\ufeff'), true)
  assert.equal(
    trimProtocolBlankCharacters('\u0085\ufeffhello\u00a0'),
    'hello',
  )
})

test('TypeScript rejects a successful response without an id', () => {
  assert.throws(
    () => parseServerMessage({
      type: 'response',
      protocol: fixtures.protocol,
      id: null,
      ok: true,
      result: {},
    }),
    ProtocolValidationError,
  )
})

function projectStateResponse() {
  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'project state response',
  )
  assert.ok(sample)
  return structuredClone(sample.message)
}

function settingsStateResponse() {
  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'settings state response',
  )
  assert.ok(sample)
  return structuredClone(sample.message)
}

function voiceSettingsStateResponse() {
  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'voice settings state response',
  )
  assert.ok(sample)
  return structuredClone(sample.message)
}

test('TypeScript accepts only exact renderer-safe transcription status', () => {
  const response = voiceSettingsStateResponse()
  const parsed = parseVoiceSettingsStateResult(response.result)

  assert.deepEqual(
    Object.keys(parsed.transcriptionStatus),
    [
      'state',
      'model',
      'requestedDevice',
      'resolvedDevice',
      'computeType',
      'reason',
    ],
  )
  assert.equal(parsed.transcriptionStatus.model, 'small')
  assert.equal(parsed.transcriptionStatus.requestedDevice, 'auto')

  for (const validStatus of [
    {
      state: 'unavailable',
      model: 'small',
      requestedDevice: 'cuda',
      resolvedDevice: null,
      computeType: null,
      reason: 'device_unavailable',
    },
    {
      state: 'ready',
      model: 'small',
      requestedDevice: 'auto',
      resolvedDevice: 'cpu',
      computeType: 'int8',
      reason: 'cuda_unavailable',
    },
    {
      state: 'ready',
      model: 'small',
      requestedDevice: 'auto',
      resolvedDevice: 'cpu',
      computeType: 'float32',
      reason: 'cuda_initialization_failed',
    },
    {
      state: 'available',
      model: 'small',
      requestedDevice: 'cuda',
      resolvedDevice: 'cuda',
      computeType: 'float16',
      reason: null,
    },
    {
      state: 'ready',
      model: 'small',
      requestedDevice: 'cpu',
      resolvedDevice: 'cpu',
      computeType: 'int8',
      reason: null,
    },
  ]) {
    const valid = voiceSettingsStateResponse()
    valid.result.transcriptionStatus = validStatus
    assert.deepEqual(
      parseVoiceSettingsStateResult(valid.result).transcriptionStatus,
      validStatus,
    )
  }

  for (const field of ['path', 'modelPath', 'message', 'native']) {
    const unsafe = voiceSettingsStateResponse()
    unsafe.result.transcriptionStatus[field] = 'D:/private/native.dll'
    assert.throws(
      () => parseVoiceSettingsStateResult(unsafe.result),
      ProtocolValidationError,
    )
  }

  for (const [field, value] of [
    ['state', 'loading'],
    ['model', 'D:/private/model'],
    ['requestedDevice', 'directml'],
    ['resolvedDevice', 'auto'],
    ['computeType', 'float64'],
    ['reason', 'native_error'],
  ]) {
    const invalid = voiceSettingsStateResponse()
    invalid.result.transcriptionStatus[field] = value
    assert.throws(
      () => parseVoiceSettingsStateResult(invalid.result),
      ProtocolValidationError,
    )
  }

  for (const inconsistentStatus of [
    {
      state: 'unavailable',
      model: 'small',
      requestedDevice: 'auto',
      resolvedDevice: 'cpu',
      computeType: 'int8',
      reason: 'model_missing',
    },
    {
      state: 'unavailable',
      model: 'small',
      requestedDevice: 'auto',
      resolvedDevice: null,
      computeType: null,
      reason: null,
    },
    {
      state: 'ready',
      model: 'small',
      requestedDevice: 'cpu',
      resolvedDevice: null,
      computeType: null,
      reason: null,
    },
    {
      state: 'available',
      model: 'small',
      requestedDevice: 'auto',
      resolvedDevice: 'cpu',
      computeType: 'int8',
      reason: 'model_missing',
    },
    {
      state: 'available',
      model: 'small',
      requestedDevice: 'auto',
      resolvedDevice: 'cpu',
      computeType: 'int8',
      reason: 'cuda_initialization_failed',
    },
    {
      state: 'ready',
      model: 'small',
      requestedDevice: 'auto',
      resolvedDevice: 'cuda',
      computeType: 'int8',
      reason: null,
    },
    {
      state: 'ready',
      model: 'small',
      requestedDevice: 'cuda',
      resolvedDevice: 'cpu',
      computeType: 'int8',
      reason: null,
    },
    {
      state: 'unavailable',
      model: 'small',
      requestedDevice: 'auto',
      resolvedDevice: null,
      computeType: null,
      reason: 'cuda_unavailable',
    },
  ]) {
    const invalid = voiceSettingsStateResponse()
    invalid.result.transcriptionStatus = inconsistentStatus
    assert.throws(
      () => parseVoiceSettingsStateResult(invalid.result),
      ProtocolValidationError,
    )
  }
})

function voiceCaptureRequest() {
  const sample = fixtures.validClientMessages.find(
    (candidate) => candidate.name === 'voice capture complete request',
  )
  assert.ok(sample)
  return structuredClone(sample.message)
}

function voiceCaptureResponse() {
  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'voice capture response',
  )
  assert.ok(sample)
  return structuredClone(sample.message)
}

test('TypeScript accepts exact canonical Voice capture PCM metadata', () => {
  const request = voiceCaptureRequest()
  const parsedParams = parseVoiceCaptureCompleteParams(request.params)

  assert.equal(parsedParams.sessionId, 'voice_fixture')
  assert.equal(Buffer.from(parsedParams.pcmBase64, 'base64').byteLength, 6_400)
  assert.deepEqual(
    createRequest(
      'voice-capture-builder',
      'voice.capture.complete',
      parsedParams,
    ).params,
    parsedParams,
  )
})

for (const invalidBase64 of [
  'AB==',
  'AA==\n',
  '_A==',
  'AQ',
  'AAAA====',
  '音频',
]) {
  test(`TypeScript rejects noncanonical Voice Base64: ${JSON.stringify(invalidBase64)}`, () => {
    const request = voiceCaptureRequest()
    request.params.pcmBase64 = invalidBase64

    assert.throws(
      () => parseClientRequest(request),
      /strict canonical Base64/,
    )
  })
}

test('TypeScript rejects Voice PCM whose decoded length is inconsistent', () => {
  const request = voiceCaptureRequest()
  request.params.pcmBase64 = Buffer.alloc(6_402).toString('base64')

  assert.throws(
    () => parseClientRequest(request),
    /sampleCount \* 2/,
  )
})

test('TypeScript bounds Voice PCM text before decoding', () => {
  const request = voiceCaptureRequest()
  request.params.pcmBase64 = 'A'.repeat(
    VOICE_CAPTURE_MAX_BASE64_CHARACTERS + 1,
  )

  assert.throws(() => parseClientRequest(request), ProtocolValidationError)
})

for (const [field, value] of [
  ['sampleCount', 3_199],
  ['sampleCount', 3_201],
  ['sampleCount', 480_320],
  ['speechStartSample', -320],
  ['speechStartSample', 1],
  ['speechStartSample', 320],
  ['speechEndSample', 3_520],
  ['speechEndSample', 3_199],
]) {
  test(`TypeScript rejects invalid Voice capture ${field}: ${value}`, () => {
    const request = voiceCaptureRequest()
    request.params[field] = value

    assert.throws(() => parseClientRequest(request), ProtocolValidationError)
  })
}

test('TypeScript parses Voice receipt without returning PCM', () => {
  const message = voiceCaptureResponse()
  const result = parseVoiceCaptureResult(message.result)

  assert.equal(result.kind, 'voice.capture')
  assert.equal(result.durationMs, 200)
  assert.equal(result.speechDurationMs, 200)
  assert.equal(Object.hasOwn(result, 'pcmBase64'), false)
})

for (const [field, value] of [
  ['durationMs', -1],
  ['durationMs', 201],
  ['speechDurationMs', 1.5],
  ['speechDurationMs', 201],
  ['sha256Hex', 'A'.repeat(64)],
  ['sampleCount', 3_201],
  ['speechEndSample', 3_520],
]) {
  test(`TypeScript rejects invalid Voice result ${field}: ${value}`, () => {
    const message = voiceCaptureResponse()
    message.result[field] = value

    assert.throws(
      () => parseVoiceCaptureResult(message.result),
      ProtocolValidationError,
    )
  })
}

test('TypeScript rejects Voice result PCM and other extra fields', () => {
  const message = voiceCaptureResponse()
  message.result.pcmBase64 = 'AAAA'

  assert.throws(
    () => parseVoiceCaptureResult(message.result),
    ProtocolValidationError,
  )
})

function voiceTranscriptionRequest() {
  const sample = fixtures.validClientMessages.find(
    (candidate) => candidate.name === 'voice transcription start request',
  )
  assert.ok(sample)
  return structuredClone(sample.message)
}

function voiceTranscriptionResponse() {
  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'voice transcription response',
  )
  assert.ok(sample)
  return structuredClone(sample.message)
}

function readyVoiceTranscriptionBackend(
  capabilities = ['voice.transcription', 'request.cancel'],
) {
  const writes = []
  const events = []
  let killCount = 0
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => {
      killCount += 1
      return true
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities,
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  return {
    backend,
    events,
    writes,
    getKillCount: () => killCount,
  }
}

test('Backend permits settings reads but gates mutations while STT drains', async () => {
  const { backend, writes } = readyVoiceTranscriptionBackend([
    'voice.transcription',
    'request.cancel',
    'settings.management',
    'voice.settings',
  ])
  const params = voiceTranscriptionRequest().params
  const { requestId } = backend.beginVoiceTranscription(params)
  const gettingSettings = backend.getSettings()
  const settingsRequest = JSON.parse(writes.at(-1))
  const gettingVoiceSettings = backend.getVoiceSettings()
  const voiceSettingsRequest = JSON.parse(writes.at(-1))
  const currentSettings = settingsStateResponse().result
  const currentVoiceSettings = voiceSettingsStateResponse().result

  const assertMutationsBlocked = async () => {
    await assert.rejects(
      backend.updateSettings({
        expectedRevision: currentSettings.revision,
        settings: currentSettings.settings,
      }),
      /voice transcription|current action/,
    )
    await assert.rejects(
      backend.updateVoiceSettings({
        expectedRevision: currentVoiceSettings.revision,
        inputDeviceId: null,
        outputDeviceId: null,
      }),
      /voice transcription/,
    )
  }

  await assertMutationsBlocked()
  const stopping = backend.stopVoiceTranscription(requestId)
  const cancelRequest = JSON.parse(writes.at(-1))
  await assertMutationsBlocked()
  assert.deepEqual(
    writes.map((wire) => JSON.parse(wire).method),
    [
      'voice.transcription.start',
      'settings.get',
      'voice.settings.get',
      'request.cancel',
    ],
  )

  const settingsResponse = settingsStateResponse()
  settingsResponse.id = settingsRequest.id
  backend.handleProtocolLine(JSON.stringify(settingsResponse))
  const voiceSettingsResponse = voiceSettingsStateResponse()
  voiceSettingsResponse.id = voiceSettingsRequest.id
  backend.handleProtocolLine(JSON.stringify(voiceSettingsResponse))
  assert.equal((await gettingSettings).revision, currentSettings.revision)
  assert.deepEqual(
    (await gettingVoiceSettings).transcriptionStatus,
    currentVoiceSettings.transcriptionStatus,
  )

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: requestId,
    ok: false,
    error: {
      code: 'request.cancelled',
      message: 'Voice transcription was cancelled.',
      retryable: false,
    },
  }))
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))
  await stopping
})

test('TypeScript accepts one-shot Voice transcription PCM and language', () => {
  const request = voiceTranscriptionRequest()
  const params = parseVoiceTranscriptionStartParams(request.params)

  assert.equal(params.language, 'auto')
  assert.equal(Buffer.from(params.pcmBase64, 'base64').byteLength, 6_400)
  assert.deepEqual(
    createRequest(
      'voice-transcription-builder',
      'voice.transcription.start',
      params,
    ).params,
    params,
  )
})

for (const language of ['', 'fr', 'ZH', null, true]) {
  test(`TypeScript rejects Voice transcription language: ${String(language)}`, () => {
    const request = voiceTranscriptionRequest()
    request.params.language = language

    assert.throws(() => parseClientRequest(request), ProtocolValidationError)
  })
}

for (const [field, value] of [
  ['pcmBase64', 'AB=='],
  ['speechEndSample', 3_199],
]) {
  test(`TypeScript rejects invalid Voice transcription capture ${field}`, () => {
    const request = voiceTranscriptionRequest()
    request.params[field] = value

    assert.throws(() => parseClientRequest(request), ProtocolValidationError)
  })
}

test('TypeScript parses a bounded Voice transcript without PCM', () => {
  const message = voiceTranscriptionResponse()
  const result = parseVoiceTranscriptionResult(message.result)

  assert.equal(result.text, '你好，世界。')
  assert.equal(result.language, 'zh')
  assert.equal(Object.hasOwn(result, 'pcmBase64'), false)
  assert.equal(Object.hasOwn(result, 'modelPath'), false)
})

for (const [field, value] of [
  ['text', ''],
  ['text', '\ufeff \t\n'],
  ['text', 'x'.repeat(VOICE_TRANSCRIPTION_MAX_TEXT_CODE_POINTS + 1)],
  ['language', 'auto'],
  ['language', 'fr'],
  ['languageProbability', Number.NaN],
  ['languageProbability', Number.POSITIVE_INFINITY],
  ['languageProbability', -0.01],
  ['languageProbability', 1.01],
]) {
  test(`TypeScript rejects invalid Voice transcript ${field}`, () => {
    const message = voiceTranscriptionResponse()
    message.result[field] = value

    assert.throws(
      () => parseVoiceTranscriptionResult(message.result),
      ProtocolValidationError,
    )
  })
}

test('TypeScript rejects private Voice transcription fields', () => {
  const message = voiceTranscriptionResponse()
  message.result.modelPath = 'D:/private/model'

  assert.throws(
    () => parseVoiceTranscriptionResult(message.result),
    ProtocolValidationError,
  )
})

for (const invalidState of [
  'active-absent',
  'active-mismatch',
  'duplicate-project',
  'dangling-chat-project',
  'wrong-chat-count',
]) {
  test(`TypeScript rejects Project state invariant: ${invalidState}`, () => {
    const message = projectStateResponse()
    const result = message.result

    if (invalidState === 'active-absent') {
      result.activeProject.projectId = 'project_missing'
    } else if (invalidState === 'active-mismatch') {
      result.activeProject.name = 'Stale Project'
    } else if (invalidState === 'duplicate-project') {
      result.projects.push(structuredClone(result.projects[0]))
    } else if (invalidState === 'dangling-chat-project') {
      result.chatState.chats[1].projectId = 'project_missing'
    } else {
      result.projects[0].chatCount = 2
      result.activeProject.chatCount = 2
    }

    assert.throws(
      () => parseServerMessage(message),
      ProtocolValidationError,
    )
  })
}

function createPendingChat(method = 'chat.stream') {
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.pendingRequests.set('chat-state-1', {
    method,
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })
  return { backend, events }
}

function streamFrame(sequence, chunk, done) {
  return JSON.stringify({
    type: 'stream',
    protocol: fixtures.protocol,
    requestId: 'chat-state-1',
    stream: 'chat.reply',
    sequence,
    chunk,
    done,
  })
}

function responseFrame(reply) {
  return JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'chat-state-1',
    ok: true,
    result: { chatId: 'chat_fixture', reply },
  })
}

function eventFrame(requestId, event, data) {
  return JSON.stringify({
    type: 'event',
    protocol: fixtures.protocol,
    event,
    requestId,
    data,
  })
}

for (const method of ['chat.stream', 'chat.retry']) {
  test(`Backend accepts Chat lifecycle for matching ${method}`, () => {
    const { backend, events } = createPendingChat(method)

    backend.handleProtocolLine(eventFrame(
      'chat-state-1',
      'chat.started',
      { chatId: 'chat_fixture' },
    ))

    assert.deepEqual(events.at(-1), {
      type: 'protocol-event',
      name: 'chat.started',
      requestId: 'chat-state-1',
      data: { chatId: 'chat_fixture' },
    })
  })
}

for (const [name, method, expectedChatId, receivedChatId] of [
  ['non-generation method', 'settings.get', undefined, 'chat_fixture'],
  ['different Chat', 'chat.stream', 'chat_fixture', 'chat_other'],
]) {
  test(`Backend rejects Chat lifecycle with ${name}`, () => {
    let killCount = 0
    const backend = new BackendProcess('.', () => undefined)
    backend.child = {
      kill: () => {
        killCount += 1
        return true
      },
    }
    backend.pendingRequests.set('chat-event-1', {
      method,
      ...(expectedChatId === undefined ? {} : { chatId: expectedChatId }),
      nextSequence: 0,
      streamCompleted: false,
      streamedReply: '',
      streamedLength: 0,
    })

    backend.handleProtocolLine(eventFrame(
      'chat-event-1',
      'chat.completed',
      { chatId: receivedChatId },
    ))

    assert.equal(killCount, 1)
    assert.equal(backend.getSnapshot().status, 'error')
    assert.match(
      backend.getSnapshot().error,
      /Chat lifecycle event is invalid/,
    )
  })
}

for (const [event, data] of [
  [
    'voice.speech.clip',
    {
      chatId: 'chat_fixture',
      clipToken: 'a'.repeat(64),
      sequence: 0,
      byteLength: 46,
      sha256: 'b'.repeat(64),
      mediaType: 'audio/wav',
    },
  ],
  [
    'voice.speech.failure',
    {
      chatId: 'chat_fixture',
      sequence: 0,
      code: 'synthesis_failed',
    },
  ],
  [
    'voice.speech.terminal',
    {
      chatId: 'chat_fixture',
      state: 'completed',
      submittedSentences: 1,
      completedSentences: 1,
      failedSentences: 0,
    },
  ],
]) {
  test(`Backend fails closed on unwired ${event}`, () => {
    const forwarded = []
    let killCount = 0
    const backend = new BackendProcess('.', (message) => {
      if (message.type === 'protocol-event') {
        forwarded.push(message)
      }
    })
    backend.child = {
      kill: () => {
        killCount += 1
        return true
      },
    }
    // Even a matching Chat generation cannot authorize speech metadata before
    // the fd3 owner and frame pairing are integrated.
    backend.pendingRequests.set('speech-event-1', {
      method: 'chat.stream',
      chatId: 'chat_fixture',
      nextSequence: 0,
      streamCompleted: false,
      streamedReply: '',
      streamedLength: 0,
    })

    backend.handleProtocolLine(eventFrame(
      'speech-event-1',
      event,
      data,
    ))

    assert.equal(killCount, 1)
    assert.equal(backend.getSnapshot().status, 'error')
    assert.match(
      backend.getSnapshot().error,
      /speech event arrived before audio delivery was enabled/,
    )
    assert.deepEqual(forwarded, [])
  })
}

test('Backend keeps wired speech correlation after Chat text completes', () => {
  const accepted = []
  const forwarded = []
  const backend = new BackendProcess('.', (message) => forwarded.push(message))
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream', 'voice.speech'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  backend.speechDelivery = {
    acceptEvent: (message) => accepted.push(message),
  }

  // The text request is deliberately absent: speech may drain after the
  // terminal Chat response has already removed it from pendingRequests.
  backend.handleProtocolLine(eventFrame(
    'speech-after-chat-1',
    'voice.speech.clip',
    {
      chatId: 'chat_fixture',
      clipToken: 'a'.repeat(64),
      sequence: 0,
      byteLength: 46,
      sha256: 'b'.repeat(64),
      mediaType: 'audio/wav',
    },
  ))

  assert.equal(accepted.length, 1)
  assert.equal(accepted[0].requestId, 'speech-after-chat-1')
  assert.deepEqual(forwarded, [])
  assert.equal(backend.getSnapshot().status, 'ready')
})

test('Backend registers speech turns only when capability is advertised', () => {
  const writes = []
  const starts = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream', 'voice.speech'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  backend.speechDelivery = {
    startTurn: (requestId, chatId) => starts.push({ requestId, chatId }),
  }

  const { requestId } = backend.beginChat({
    chatId: 'chat_fixture',
    message: 'Speak this reply.',
    attachmentIds: [],
  })

  assert.deepEqual(starts, [{ requestId, chatId: 'chat_fixture' }])
  assert.equal(JSON.parse(writes.at(-1)).id, requestId)

  const ungatedStarts = []
  const ungatedBackend = new BackendProcess('.', () => undefined)
  ungatedBackend.child = {
    stdin: { writable: true, write: () => true },
  }
  ungatedBackend.snapshot = {
    ...backend.snapshot,
    capabilities: ['chat.stream'],
  }
  ungatedBackend.speechDelivery = {
    startTurn: (...parameters) => ungatedStarts.push(parameters),
  }
  ungatedBackend.beginChat({
    chatId: 'chat_fixture',
    message: 'Text only.',
    attachmentIds: [],
  })
  assert.deepEqual(ungatedStarts, [])
})

test('Backend forwards only renderer-safe speech status metadata', () => {
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))

  backend.emitSpeechDeliveryStatus({
    kind: 'playing',
    requestId: 'speech-request-1',
    chatId: 'chat_fixture',
    sequence: 0,
  })
  backend.emitSpeechDeliveryStatus({
    kind: 'terminal',
    requestId: 'speech-request-1',
    chatId: 'chat_fixture',
    state: 'completed',
  })

  assert.deepEqual(events, [
    {
      type: 'voice-speech-status',
      kind: 'playing',
      requestId: 'speech-request-1',
      chatId: 'chat_fixture',
      sequence: 0,
    },
    {
      type: 'voice-speech-status',
      kind: 'terminal',
      requestId: 'speech-request-1',
      chatId: 'chat_fixture',
      state: 'completed',
    },
  ])
  assert.doesNotMatch(
    JSON.stringify(events),
    /wav|clipToken|sha256|text|path|diagnostic/iu,
  )
})

test('Backend can stop speech after Chat text ownership has ended', () => {
  const cancelled = []
  const backend = new BackendProcess('.', () => undefined)
  backend.speechDelivery = {
    cancelTurn: (requestId) => cancelled.push(requestId),
  }

  backend.stopSpeechPlayback('speech-after-chat-1')

  assert.deepEqual(cancelled, ['speech-after-chat-1'])
})

test('Backend can retire current speech when Renderer ownership resets', () => {
  let cancelled = 0
  const backend = new BackendProcess('.', () => undefined)
  backend.speechDelivery = {
    cancelCurrentTurn: () => { cancelled += 1 },
  }

  backend.stopCurrentSpeechPlayback()

  assert.equal(cancelled, 1)
})

test('Backend removes unavailable speech from renderer capabilities', () => {
  const events = []
  let disposed = 0
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream', 'voice.speech'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  backend.speechDelivery = {
    dispose: () => { disposed += 1 },
  }

  backend.disableSpeechDelivery()

  assert.equal(disposed, 1)
  assert.deepEqual(backend.snapshot.capabilities, ['chat.stream', 'voice.speech'])
  assert.deepEqual(backend.getSnapshot().capabilities, ['chat.stream'])
  assert.deepEqual(events.at(-1).snapshot.capabilities, ['chat.stream'])
})

test('Backend state machine accepts one ordered matching Chat stream', () => {
  const { backend, events } = createPendingChat()

  backend.handleProtocolLine(streamFrame(0, '你', false))
  backend.handleProtocolLine(streamFrame(1, '好', false))
  backend.handleProtocolLine(streamFrame(2, '', true))
  backend.handleProtocolLine(responseFrame('你好'))

  assert.deepEqual(
    events.map((event) => event.type),
    ['chat-chunk', 'chat-chunk', 'chat-complete'],
  )
})

test('Backend state machine accepts a retry over the Chat reply stream', () => {
  const { backend, events } = createPendingChat('chat.retry')

  backend.handleProtocolLine(streamFrame(0, 'replacement', false))
  backend.handleProtocolLine(streamFrame(1, '', true))
  backend.handleProtocolLine(responseFrame('replacement'))

  assert.deepEqual(
    events.map((event) => event.type),
    ['chat-chunk', 'chat-complete'],
  )
})

test('Backend sends an exact retry request and tracks it as generation', () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.retry'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const { requestId } = backend.beginRetry({
    chatId: 'chat_fixture',
    userMessageId: 'message_user',
    assistantMessageId: 'message_assistant',
    message: '  Edited prompt  ',
  })
  const request = JSON.parse(writes.at(-1))

  assert.equal(request.id, requestId)
  assert.equal(request.method, 'chat.retry')
  assert.deepEqual(request.params, {
    chatId: 'chat_fixture',
    userMessageId: 'message_user',
    assistantMessageId: 'message_assistant',
    message: 'Edited prompt',
  })
  assert.equal(backend.pendingRequests.get(requestId).method, 'chat.retry')
  assert.deepEqual(backend.getSnapshot().activeGeneration, {
    requestId,
    chatId: 'chat_fixture',
    kind: 'retry',
    userText: 'Edited prompt',
    userMessageId: 'message_user',
    assistantMessageId: 'message_assistant',
    reply: '',
    stopping: false,
  })
})

test('Backend snapshot preserves an in-flight reply through stop completion', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream', 'request.cancel'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const { requestId } = backend.beginChat({
    chatId: 'chat_fixture',
    message: '  Continue after reload.  ',
    attachmentIds: [],
  })
  assert.deepEqual(backend.getSnapshot().activeGeneration, {
    requestId,
    chatId: 'chat_fixture',
    kind: 'send',
    userText: 'Continue after reload.',
    reply: '',
    stopping: false,
  })

  backend.handleProtocolLine(JSON.stringify({
    type: 'stream',
    protocol: fixtures.protocol,
    requestId,
    stream: 'chat.reply',
    sequence: 0,
    chunk: '继续',
    done: false,
  }))
  assert.equal(backend.getSnapshot().activeGeneration.reply, '继续')

  const stopping = backend.stopGeneration(requestId)
  const cancelRequest = JSON.parse(writes.at(-1))
  assert.equal(backend.getSnapshot().activeGeneration.stopping, true)
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))
  await stopping
  assert.equal(backend.getSnapshot().activeGeneration.stopping, true)

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: requestId,
    ok: false,
    error: {
      code: 'request.cancelled',
      message: 'Generation was stopped.',
      retryable: false,
    },
  }))
  assert.equal(backend.getSnapshot().activeGeneration, undefined)
})

test('Backend sends an attachment-only Chat request without paths', () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream', 'attachment.management'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const { requestId } = backend.beginChat({
    chatId: 'chat_fixture',
    message: '',
    attachmentIds: ['attachment_fixture'],
  })
  const request = JSON.parse(writes.at(-1))
  assert.equal(request.id, requestId)
  assert.equal(request.method, 'chat.stream')
  assert.deepEqual(request.params, {
    chatId: 'chat_fixture',
    message: '',
    attachmentIds: ['attachment_fixture'],
  })
  assert.equal(JSON.stringify(request).includes('sourcePath'), false)
})

test('Backend stop request settles independently from cancelled generation', async () => {
  const writes = []
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.pendingRequests.set('generation-1', {
    method: 'chat.retry',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  const stopping = backend.stopGeneration('generation-1')
  const cancelRequest = JSON.parse(writes.at(-1))
  assert.equal(cancelRequest.method, 'request.cancel')
  assert.deepEqual(cancelRequest.params, { requestId: 'generation-1' })

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))
  await stopping
  assert.equal(backend.pendingRequests.has('generation-1'), true)

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'generation-1',
    ok: false,
    error: {
      code: 'request.cancelled',
      message: 'Generation was stopped.',
      retryable: false,
    },
  }))
  assert.deepEqual(events.at(-1), {
    type: 'chat-error',
    requestId: 'generation-1',
    chatId: 'chat_fixture',
    code: 'request.cancelled',
    message: 'Generation was stopped.',
    retryable: false,
  })
})

test('Backend rejects success after cancellation was accepted', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => undefined,
  }
  backend.pendingRequests.set('generation-cancelled', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  const stopping = backend.stopGeneration('generation-cancelled')
  const cancelRequest = JSON.parse(writes.at(-1))
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))
  await stopping

  backend.handleProtocolLine(JSON.stringify({
    type: 'stream',
    protocol: fixtures.protocol,
    requestId: 'generation-cancelled',
    stream: 'chat.reply',
    sequence: 0,
    chunk: '',
    done: true,
  }))

  assert.equal(backend.getSnapshot().status, 'error')
  assert.match(
    backend.getSnapshot().error,
    /completed a stream after accepting its cancellation/,
  )
})

test('Backend withholds success that precedes a contradictory cancel ack', async () => {
  const writes = []
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => undefined,
  }
  backend.pendingRequests.set('generation-raced', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  const stopping = backend.stopGeneration('generation-raced')
  const cancelRequest = JSON.parse(writes.at(-1))
  backend.handleProtocolLine(JSON.stringify({
    type: 'stream',
    protocol: fixtures.protocol,
    requestId: 'generation-raced',
    stream: 'chat.reply',
    sequence: 0,
    chunk: 'Too late',
    done: false,
  }))
  backend.handleProtocolLine(JSON.stringify({
    type: 'stream',
    protocol: fixtures.protocol,
    requestId: 'generation-raced',
    stream: 'chat.reply',
    sequence: 1,
    chunk: '',
    done: true,
  }))
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'generation-raced',
    ok: true,
    result: { chatId: 'chat_fixture', reply: 'Too late' },
  }))

  assert.equal(
    events.some((event) => event.type === 'chat-complete'),
    false,
  )

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))

  await assert.rejects(stopping, /non-cancelled generation/)
  assert.equal(
    events.some((event) => event.type === 'chat-complete'),
    false,
  )
  assert.equal(backend.getSnapshot().status, 'error')
  assert.match(
    backend.getSnapshot().error,
    /non-cancelled generation after accepting cancellation/,
  )
})

test('Backend releases deferred success when cancellation is rejected', async () => {
  const writes = []
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.pendingRequests.set('generation-committed', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 1,
    streamCompleted: true,
    streamedReply: 'Committed reply',
    streamedLength: 15,
  })

  const stopping = backend.stopGeneration('generation-committed')
  const cancelRequest = JSON.parse(writes.at(-1))
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'generation-committed',
    ok: true,
    result: { chatId: 'chat_fixture', reply: 'Committed reply' },
  }))
  assert.equal(events.length, 0)

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: false,
    error: {
      code: 'request.not_cancellable',
      message: 'Generation already committed.',
      retryable: false,
    },
  }))

  await assert.rejects(stopping, /already committed/)
  assert.deepEqual(events.at(-1), {
    type: 'chat-complete',
    requestId: 'generation-committed',
    chatId: 'chat_fixture',
    reply: 'Committed reply',
  })
})

test('Backend rolls back pending generation when stdin write throws', () => {
  const privateDiagnostic = 'D:/private/python.exe write EOF'
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: () => { throw new Error(privateDiagnostic) },
    },
    kill: () => undefined,
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  assert.throws(
    () => backend.beginChat({
      chatId: 'chat_fixture',
      message: 'Hello',
      attachmentIds: [],
    }),
    /Could not write to the Python Backend/,
  )
  assert.equal(backend.pendingRequests.size, 0)
  assert.equal(backend.getSnapshot().status, 'error')
  assert.equal(
    backend.getSnapshot().error,
    'Python Backend input failed.',
  )
  assert.doesNotMatch(backend.getSnapshot().error, /private|python\.exe/u)
})

test('Backend protocol failure rejects pending renderer actions before exit', async () => {
  const writes = []
  const events = []
  let killCount = 0
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => {
      killCount += 1
      return true
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: [
      'chat.sessions',
      'project.management',
      'voice.capture',
      'request.cancel',
    ],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const chatAction = backend.openChat('chat_other')
  const projectAction = backend.listProjects()
  const settingsAction = backend.getSettings()
  const attachmentAction = backend.listAttachments({
    kind: 'chat',
    id: 'chat_fixture',
  })
  const voiceAction = backend.submitVoiceCapture(
    voiceCaptureRequest().params,
  )
  const voiceRequest = JSON.parse(writes.at(-1))
  const voicePending = backend.pendingRequests.get(voiceRequest.id)
  assert.ok(voicePending.timeout)
  backend.pendingRequests.set('generation-pending', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })
  const cancellation = backend.stopGeneration('generation-pending')
  const rejections = [
    chatAction,
    projectAction,
    settingsAction,
    attachmentAction,
    voiceAction,
    cancellation,
  ].map(
    (action) => assert.rejects(action, /Protocol connection failed/),
  )

  assert.equal(
    backend.getSnapshot().activeGeneration.requestId,
    'generation-pending',
  )

  backend.protocolFailure('Protocol connection failed.')

  await Promise.all(rejections)
  assert.equal(voicePending.timeout, undefined)
  assert.equal(killCount, 1)
  assert.equal(backend.pendingRequests.size, 0)
  const errorSnapshot = events.at(-1)
  assert.equal(errorSnapshot.type, 'snapshot')
  assert.equal(errorSnapshot.snapshot.status, 'error')
  assert.equal(
    Object.hasOwn(errorSnapshot.snapshot, 'activeGeneration'),
    false,
  )
  assert.equal(
    Object.hasOwn(backend.getSnapshot(), 'activeGeneration'),
    false,
  )
})

test('Backend allows opening another Chat while generation remains tracked', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream', 'chat.sessions'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  backend.pendingRequests.set('generation-a', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  const opening = backend.openChat('chat_second')
  const openRequest = JSON.parse(writes.at(-1))
  assert.equal(openRequest.method, 'chat.open')
  assert.deepEqual(openRequest.params, { chatId: 'chat_second' })

  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'chat state response',
  )
  const response = structuredClone(sample.message)
  response.id = openRequest.id
  response.result.activeChat = {
    ...response.result.chats[1],
    messages: [],
  }
  backend.handleProtocolLine(JSON.stringify(response))

  const state = await opening
  assert.equal(state.activeChat.chatId, 'chat_second')
  assert.equal(backend.pendingRequests.has('generation-a'), true)
  assert.equal(backend.getSnapshot().chatId, 'chat_second')
})

test('Backend permits collection reads but blocks writes during generation', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: [
      'chat.stream',
      'chat.sessions',
      'project.management',
    ],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const { requestId } = backend.beginChat({
    chatId: 'chat_fixture',
    message: 'Keep reading state while I generate.',
    attachmentIds: [],
  })
  const listingChats = backend.listChats(false)
  const listingProjects = backend.listProjects()

  await assert.rejects(
    backend.renameChat({ chatId: 'chat_fixture', title: 'Busy Chat' }),
    /active Chat reply/,
  )
  await assert.rejects(
    backend.openProject('project_second'),
    /active Chat reply/,
  )

  const requests = writes.map((wireRequest) => JSON.parse(wireRequest))
  assert.deepEqual(
    requests.map((request) => request.method),
    ['chat.stream', 'chat.list', 'project.list'],
  )
  assert.deepEqual(requests[1].params, { includeArchived: false })
  assert.deepEqual(requests[2].params, {})

  const chatSample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'chat state response',
  )
  assert.ok(chatSample)
  const chatResponse = structuredClone(chatSample.message)
  chatResponse.id = requests[1].id
  backend.handleProtocolLine(JSON.stringify(chatResponse))

  const projectResponse = projectStateResponse()
  projectResponse.id = requests[2].id
  backend.handleProtocolLine(JSON.stringify(projectResponse))

  const [chatState, projectState] = await Promise.all([
    listingChats,
    listingProjects,
  ])
  assert.equal(chatState.activeChat.chatId, 'chat_fixture')
  assert.equal(projectState.activeProject.projectId, 'project_fixture')
  assert.equal(backend.getSnapshot().activeGeneration.requestId, requestId)
})

test('Backend state machine rejects a stream sequence gap', () => {
  const { backend, events } = createPendingChat()

  backend.handleProtocolLine(streamFrame(1, 'out of order', false))

  assert.equal(events.at(-1).type, 'snapshot')
  assert.equal(events.at(-1).snapshot.status, 'error')
})

test('Backend state machine rejects a response that differs from chunks', () => {
  const { backend, events } = createPendingChat()

  backend.handleProtocolLine(streamFrame(0, 'visible', false))
  backend.handleProtocolLine(streamFrame(1, '', true))
  backend.handleProtocolLine(responseFrame('persisted differently'))

  assert.equal(events.at(-1).type, 'snapshot')
  assert.match(events.at(-1).snapshot.error, /streamed reply/)
})

test('Backend state machine rejects data after a terminal chunk', () => {
  const { backend, events } = createPendingChat()

  backend.handleProtocolLine(streamFrame(0, '', true))
  backend.handleProtocolLine(streamFrame(1, 'late', false))

  assert.equal(events.at(-1).type, 'snapshot')
  assert.equal(events.at(-1).snapshot.status, 'error')
})

test('Backend state machine resolves a typed Chat session action', async () => {
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.sessions'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Old title',
  }
  let resolveState
  let rejectState
  const statePromise = new Promise((resolve, reject) => {
    resolveState = resolve
    rejectState = reject
  })
  backend.pendingRequests.set('chat-list-1', {
    method: 'chat.list',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    resolveChatState: resolveState,
    rejectChatState: rejectState,
  })
  const sample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'chat state response',
  )
  assert.ok(sample)

  backend.handleProtocolLine(JSON.stringify(sample.message))
  const state = await statePromise

  assert.equal(state.activeChat.chatId, 'chat_fixture')
  assert.equal(backend.getSnapshot().chatTitle, 'Elysia Chat')
  assert.equal(events.at(-1).type, 'snapshot')
})

test('Backend state machine resolves a typed Project action atomically', async () => {
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.sessions', 'project.management'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Old title',
  }
  let resolveState
  let rejectState
  const statePromise = new Promise((resolve, reject) => {
    resolveState = resolve
    rejectState = reject
  })
  backend.pendingRequests.set('project-list-1', {
    method: 'project.list',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    resolveProjectState: resolveState,
    rejectProjectState: rejectState,
  })
  const response = projectStateResponse()
  response.id = 'project-list-1'

  backend.handleProtocolLine(JSON.stringify(response))
  const state = await statePromise

  assert.equal(state.activeProject.projectId, 'project_fixture')
  assert.equal(state.chatState.activeChat.chatId, 'chat_fixture')
  assert.equal(backend.getSnapshot().chatTitle, 'Elysia Chat')
  assert.equal(events.at(-1).type, 'snapshot')
})

test('Backend sends and strictly resolves typed Settings actions', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['settings.management'],
    models: ['qwen3.5:9b', 'llama3.2:3b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const getting = backend.getSettings()
  const getRequest = JSON.parse(writes.at(-1))
  assert.equal(getRequest.method, 'settings.get')
  assert.deepEqual(getRequest.params, {})

  const getResponse = settingsStateResponse()
  getResponse.id = getRequest.id
  backend.handleProtocolLine(JSON.stringify(getResponse))
  const state = await getting
  assert.deepEqual(state, getResponse.result)

  const update = {
    expectedRevision: state.revision,
    settings: {
      ...state.settings,
      memoryRetrievalLimit: 9,
    },
  }
  const updating = backend.updateSettings(update)
  const updateRequest = JSON.parse(writes.at(-1))
  assert.equal(updateRequest.method, 'settings.update')
  assert.deepEqual(updateRequest.params, update)

  const updateResponse = settingsStateResponse()
  updateResponse.id = updateRequest.id
  updateResponse.result.revision = state.revision + 1
  updateResponse.result.settings = update.settings
  backend.handleProtocolLine(JSON.stringify(updateResponse))
  const updated = await updating
  assert.deepEqual(updated, updateResponse.result)
})

test('Backend sends typed Voice Settings actions during generation', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['chat.stream', 'voice.settings'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  backend.pendingRequests.set('voice-generation', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  const getting = backend.getVoiceSettings()
  const getRequest = JSON.parse(writes.at(-1))
  assert.equal(getRequest.method, 'voice.settings.get')
  assert.deepEqual(getRequest.params, {})

  const getResponse = voiceSettingsStateResponse()
  getResponse.id = getRequest.id
  backend.handleProtocolLine(JSON.stringify(getResponse))
  const state = await getting
  const expectedState = { ...getResponse.result }
  delete expectedState.kind
  assert.deepEqual(state, expectedState)

  const update = {
    expectedRevision: state.revision,
    inputDeviceId: 'microphone_exact',
    outputDeviceId: null,
  }
  const updating = backend.updateVoiceSettings(update)
  const updateRequest = JSON.parse(writes.at(-1))
  assert.equal(updateRequest.method, 'voice.settings.update')
  assert.deepEqual(updateRequest.params, update)

  const updateResponse = voiceSettingsStateResponse()
  updateResponse.id = updateRequest.id
  updateResponse.result.revision = state.revision + 1
  updateResponse.result.inputDeviceId = update.inputDeviceId
  updateResponse.result.outputDeviceId = update.outputDeviceId
  backend.handleProtocolLine(JSON.stringify(updateResponse))
  const expectedUpdate = { ...updateResponse.result }
  delete expectedUpdate.kind
  assert.deepEqual(await updating, expectedUpdate)
  assert.equal(backend.pendingRequests.has('voice-generation'), true)
})

test('Backend sends and strictly resolves one transient Voice capture', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['voice.capture'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  const params = voiceCaptureRequest().params

  const submitting = backend.submitVoiceCapture(params)
  const request = JSON.parse(writes.at(-1))
  const pending = backend.pendingRequests.get(request.id)
  assert.equal(request.method, 'voice.capture.complete')
  assert.deepEqual(request.params, params)
  assert.ok(pending.timeout)
  assert.equal(
    Object.hasOwn(pending, 'pcmBase64'),
    false,
  )

  const response = voiceCaptureResponse()
  response.id = request.id
  backend.handleProtocolLine(JSON.stringify(response))
  const expectedReceipt = { ...response.result }
  delete expectedReceipt.kind
  assert.deepEqual(await submitting, expectedReceipt)
  assert.equal(pending.timeout, undefined)
  assert.equal(backend.pendingRequests.has(request.id), false)
  assert.equal(Object.hasOwn(expectedReceipt, 'pcmBase64'), false)
})

test('Backend times out Voice validation without disturbing other requests', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['voice.capture', 'voice.settings'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const submitting = backend.submitVoiceCapture(voiceCaptureRequest().params)
  const voiceRequest = JSON.parse(writes.at(-1))
  const voicePending = backend.pendingRequests.get(voiceRequest.id)
  assert.ok(voicePending.timeout)
  assert.equal(
    Object.hasOwn(voicePending.voiceCaptureRequest, 'pcmBase64'),
    false,
  )

  const gettingSettings = backend.getVoiceSettings()
  const settingsRequest = JSON.parse(writes.at(-1))
  const timedOut = assert.rejects(
    submitting,
    /voice capture validation timed out/,
  )
  context.mock.timers.runAll()
  await timedOut

  assert.equal(voicePending.timeout, undefined)
  assert.equal(voicePending.voiceCaptureRequest, undefined)
  assert.equal(backend.pendingRequests.has(voiceRequest.id), false)
  assert.equal(backend.pendingRequests.has(settingsRequest.id), true)
  assert.equal(backend.getSnapshot().status, 'ready')

  const lateVoiceResponse = voiceCaptureResponse()
  lateVoiceResponse.id = voiceRequest.id
  backend.handleProtocolLine(JSON.stringify(lateVoiceResponse))
  assert.equal(backend.getSnapshot().status, 'ready')
  assert.equal(backend.pendingRequests.has(settingsRequest.id), true)

  const settingsResponse = voiceSettingsStateResponse()
  settingsResponse.id = settingsRequest.id
  backend.handleProtocolLine(JSON.stringify(settingsResponse))
  const expectedSettings = { ...settingsResponse.result }
  delete expectedSettings.kind
  assert.deepEqual(await gettingSettings, expectedSettings)
  assert.equal(backend.pendingRequests.size, 0)
})

test('Backend rejects mismatched Voice receipt and clears transient actions', async () => {
  const writes = []
  let killCount = 0
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => {
      killCount += 1
      return true
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['voice.capture'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const submitting = backend.submitVoiceCapture(voiceCaptureRequest().params)
  const request = JSON.parse(writes.at(-1))
  const response = voiceCaptureResponse()
  response.id = request.id
  response.result.sampleCount += 320
  response.result.durationMs += 20
  backend.handleProtocolLine(JSON.stringify(response))

  await assert.rejects(submitting, /does not match its request/)
  assert.equal(killCount, 1)
  assert.equal(backend.getSnapshot().status, 'error')
  assert.equal(backend.pendingRequests.size, 0)
})

test('Backend blocks Voice capture while a Chat reply is active', async () => {
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: { writable: true, write: () => undefined },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['voice.capture'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  backend.pendingRequests.set('active-generation', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  await assert.rejects(
    backend.submitVoiceCapture(voiceCaptureRequest().params),
    /current Chat or voice action/,
  )
  assert.equal(backend.pendingRequests.size, 1)
})

test('Backend starts one correlated Voice transcript without retaining PCM', () => {
  const { backend, events, writes } = readyVoiceTranscriptionBackend()
  const params = voiceTranscriptionRequest().params

  const { requestId } = backend.beginVoiceTranscription(params)
  const request = JSON.parse(writes.at(-1))
  const pending = backend.pendingRequests.get(requestId)
  const expectedMetadata = { ...params }
  delete expectedMetadata.pcmBase64

  assert.equal(request.id, requestId)
  assert.equal(request.method, 'voice.transcription.start')
  assert.deepEqual(request.params, params)
  assert.deepEqual(pending.voiceTranscriptionRequest, expectedMetadata)
  assert.equal(JSON.stringify(pending).includes('pcmBase64'), false)

  backend.handleProtocolLine(JSON.stringify({
    type: 'progress',
    protocol: fixtures.protocol,
    requestId,
    operation: 'voice.transcribe',
    completed: 0,
    total: 1,
    message: 'D:/private/model is loading',
  }))
  backend.handleProtocolLine(JSON.stringify({
    type: 'event',
    protocol: fixtures.protocol,
    event: 'voice.transcription.started',
    requestId,
    data: {
      sessionId: params.sessionId,
      chatId: params.chatId,
    },
  }))

  const response = voiceTranscriptionResponse()
  response.id = requestId
  backend.handleProtocolLine(JSON.stringify(response))

  const expectedResult = { ...response.result }
  delete expectedResult.kind
  assert.deepEqual(events, [
    {
      type: 'progress',
      requestId,
      operation: 'voice.transcribe',
      completed: 0,
      total: 1,
      message: 'Transcribing audio',
    },
    {
      type: 'voice-transcription-complete',
      requestId,
      ...expectedResult,
    },
  ])
  assert.equal(backend.pendingRequests.has(requestId), false)
  assert.equal(JSON.stringify(events).includes('pcmBase64'), false)
  assert.equal(JSON.stringify(events).includes('private'), false)
})

test('Backend gates Voice transcription by capability and active Chat', () => {
  const unsupported = readyVoiceTranscriptionBackend([])
  assert.throws(
    () => unsupported.backend.beginVoiceTranscription(
      voiceTranscriptionRequest().params,
    ),
    /does not support voice transcription/,
  )
  assert.equal(unsupported.writes.length, 0)

  const mismatched = readyVoiceTranscriptionBackend()
  const params = voiceTranscriptionRequest().params
  params.chatId = 'chat_other'
  assert.throws(
    () => mismatched.backend.beginVoiceTranscription(params),
    /requested Chat is not active/,
  )
  assert.equal(mismatched.writes.length, 0)
})

test('Backend serializes Voice transcription with Chat and capture actions', async () => {
  const activeTranscript = readyVoiceTranscriptionBackend([
    'voice.transcription',
    'voice.capture',
    'chat.stream',
    'chat.retry',
  ])
  const { requestId } = activeTranscript.backend.beginVoiceTranscription(
    voiceTranscriptionRequest().params,
  )

  assert.throws(
    () => activeTranscript.backend.beginChat({
      chatId: 'chat_fixture',
      message: 'Do not race STT.',
      attachmentIds: [],
    }),
    /voice transcription/,
  )
  assert.throws(
    () => activeTranscript.backend.beginRetry({
      chatId: 'chat_fixture',
      userMessageId: 'message_user',
      assistantMessageId: 'message_assistant',
    }),
    /voice transcription/,
  )
  await assert.rejects(
    activeTranscript.backend.submitVoiceCapture(voiceCaptureRequest().params),
    /current Chat or voice action/,
  )
  await assert.rejects(
    activeTranscript.backend.createChat({ title: 'Blocked', mode: 'chat' }),
    /voice transcription/,
  )
  await assert.rejects(
    activeTranscript.backend.moveChatToProject({
      chatId: 'chat_fixture',
      projectId: 'project_fixture',
    }),
    /voice transcription/,
  )
  await assert.rejects(
    activeTranscript.backend.addAttachments(
      { kind: 'chat', id: 'chat_fixture' },
      ['D:\\input.txt'],
    ),
    /attachment action/,
  )
  assert.throws(
    () => activeTranscript.backend.beginVoiceTranscription(
      voiceTranscriptionRequest().params,
    ),
    /current Chat or voice action/,
  )
  await assert.rejects(
    activeTranscript.backend.stopGeneration(requestId),
    /generation is not in progress/,
  )

  const activeChat = readyVoiceTranscriptionBackend([
    'voice.transcription',
  ])
  activeChat.backend.pendingRequests.set('chat-active', {
    method: 'chat.stream',
    chatId: 'chat_fixture',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })
  assert.throws(
    () => activeChat.backend.beginVoiceTranscription(
      voiceTranscriptionRequest().params,
    ),
    /current Chat or voice action/,
  )
  await assert.rejects(
    activeChat.backend.stopVoiceTranscription('chat-active'),
    /voice transcription is not in progress/,
  )

  const allowedReads = readyVoiceTranscriptionBackend([
    'voice.transcription',
  ])
  allowedReads.backend.beginVoiceTranscription(
    voiceTranscriptionRequest().params,
  )
  void allowedReads.backend.listChats(false)
  void allowedReads.backend.openChat('chat_other')
  void allowedReads.backend.listProjects()
  void allowedReads.backend.openProject('project_fixture')
  void allowedReads.backend.listAttachments({
    kind: 'chat',
    id: 'chat_fixture',
  })
  assert.deepEqual(
    allowedReads.writes.slice(1).map((wire) => JSON.parse(wire).method),
    [
      'chat.list',
      'chat.open',
      'project.list',
      'project.open',
      'attachment.list',
    ],
  )

  const activeCapture = readyVoiceTranscriptionBackend([
    'voice.transcription',
  ])
  activeCapture.backend.pendingRequests.set('capture-active', {
    method: 'voice.capture.complete',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })
  assert.throws(
    () => activeCapture.backend.beginVoiceTranscription(
      voiceTranscriptionRequest().params,
    ),
    /current Chat or voice action/,
  )
})

test('Backend rejects a mismatched Voice transcript correlation', () => {
  const {
    backend,
    events,
    getKillCount,
  } = readyVoiceTranscriptionBackend()
  const { requestId } = backend.beginVoiceTranscription(
    voiceTranscriptionRequest().params,
  )
  const response = voiceTranscriptionResponse()
  response.id = requestId
  response.result.chatId = 'chat_other'

  backend.handleProtocolLine(JSON.stringify(response))

  assert.equal(
    events.some((event) => event.type === 'voice-transcription-complete'),
    false,
  )
  assert.equal(backend.getSnapshot().status, 'error')
  assert.match(backend.getSnapshot().error, /does not match its request/)
  assert.equal(getKillCount(), 1)
})

test('Backend replaces private Voice transcription errors with fixed text', () => {
  const { backend, events } = readyVoiceTranscriptionBackend()
  const params = voiceTranscriptionRequest().params
  const { requestId } = backend.beginVoiceTranscription(params)

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: requestId,
    ok: false,
    error: {
      code: 'voice.transcription.failed',
      message: 'D:/private/model/native.dll failed',
      retryable: true,
    },
  }))

  assert.deepEqual(events, [{
    type: 'voice-transcription-error',
    requestId,
    sessionId: params.sessionId,
    chatId: params.chatId,
    code: 'voice.transcription.failed',
    message: 'Local voice transcription failed.',
    retryable: true,
  }])
  assert.equal(JSON.stringify(events).includes('private'), false)
})

test('Backend defers cancelled Voice terminal output until cancel ack', async () => {
  const { backend, events, writes } = readyVoiceTranscriptionBackend()
  const params = voiceTranscriptionRequest().params
  const { requestId } = backend.beginVoiceTranscription(params)
  const stopping = backend.stopVoiceTranscription(requestId)
  const cancelRequest = JSON.parse(writes.at(-1))

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: requestId,
    ok: false,
    error: {
      code: 'request.cancelled',
      message: 'Voice transcription was cancelled.',
      retryable: false,
    },
  }))
  assert.equal(events.length, 0)

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))
  await stopping

  assert.deepEqual(events, [{
    type: 'voice-transcription-error',
    requestId,
    sessionId: params.sessionId,
    chatId: params.chatId,
    code: 'request.cancelled',
    message: 'Voice transcription was cancelled.',
    retryable: false,
  }])
  assert.equal(backend.pendingRequests.size, 0)
})

test('Backend releases Voice success when cancellation loses the race', async () => {
  const { backend, events, writes } = readyVoiceTranscriptionBackend()
  const { requestId } = backend.beginVoiceTranscription(
    voiceTranscriptionRequest().params,
  )
  const stopping = backend.stopVoiceTranscription(requestId)
  const cancelRequest = JSON.parse(writes.at(-1))
  const response = voiceTranscriptionResponse()
  response.id = requestId

  backend.handleProtocolLine(JSON.stringify(response))
  assert.equal(events.length, 0)

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: false,
    error: {
      code: 'request.not_cancellable',
      message: 'Voice transcription already completed.',
      retryable: false,
    },
  }))

  await assert.rejects(stopping, /already completed/)
  assert.equal(events.length, 1)
  assert.equal(events[0].type, 'voice-transcription-complete')
  assert.equal(events[0].requestId, requestId)
  assert.equal(backend.getSnapshot().status, 'ready')
})

test('Backend fails if accepted Voice cancellation never becomes terminal', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const {
    backend,
    getKillCount,
    writes,
  } = readyVoiceTranscriptionBackend()
  const { requestId } = backend.beginVoiceTranscription(
    voiceTranscriptionRequest().params,
  )
  const stopping = backend.stopVoiceTranscription(requestId)
  const cancelRequest = JSON.parse(writes.at(-1))

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: cancelRequest.id,
    ok: true,
    result: { stopped: true },
  }))
  await stopping
  assert.ok(backend.pendingRequests.get(requestId).timeout)

  context.mock.timers.runAll()

  assert.equal(backend.getSnapshot().status, 'error')
  assert.match(
    backend.getSnapshot().error,
    /Cancelled voice transcription did not reach a terminal response/,
  )
  assert.equal(getKillCount(), 1)
})

test('Backend sends and strictly resolves scoped Attachment actions', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['attachment.management'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  const scope = { kind: 'chat', id: 'chat_fixture' }
  const emptyState = {
    scope,
    attachments: [],
    maxFileBytes: 16_777_216,
    maxFileCount: 10,
  }

  const listing = backend.listAttachments(scope)
  const listRequest = JSON.parse(writes.at(-1))
  assert.equal(listRequest.method, 'attachment.list')
  assert.deepEqual(listRequest.params, { scope })
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: listRequest.id,
    ok: true,
    result: emptyState,
  }))
  assert.deepEqual(await listing, emptyState)

  const sourcePaths = [String.raw`C:\Users\Actor\Documents\notes.txt`]
  const adding = backend.addAttachments(scope, sourcePaths)
  const addRequest = JSON.parse(writes.at(-1))
  assert.equal(addRequest.method, 'attachment.add')
  assert.deepEqual(addRequest.params, { scope, sourcePaths })
  assert.throws(
    () => backend.beginChat({
      chatId: 'chat_fixture',
      message: 'Do not race this attachment write.',
      attachmentIds: [],
    }),
    /attachment action/u,
  )
  const readyState = {
    ...emptyState,
    attachments: [
      {
        attachmentId: 'attachment_fixture',
        fileName: 'notes.txt',
        mediaType: 'text/plain',
        sizeBytes: 12,
        status: 'ready',
      },
    ],
  }
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: addRequest.id,
    ok: true,
    result: readyState,
  }))
  assert.deepEqual(await adding, readyState)

  const removing = backend.removeAttachment(
    scope,
    'attachment_fixture',
  )
  const removeRequest = JSON.parse(writes.at(-1))
  assert.equal(removeRequest.method, 'attachment.remove')
  assert.deepEqual(removeRequest.params, {
    scope,
    attachmentId: 'attachment_fixture',
  })
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: removeRequest.id,
    ok: true,
    result: emptyState,
  }))
  assert.deepEqual(await removing, emptyState)
})

test('Backend allows concurrent Attachment reads while serializing writes', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['attachment.management'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }
  const chatScope = { kind: 'chat', id: 'chat_fixture' }
  const projectScope = { kind: 'project', id: 'project_fixture' }

  const chatListing = backend.listAttachments(chatScope)
  const projectListing = backend.listAttachments(projectScope)
  await assert.rejects(
    backend.addAttachments(
      chatScope,
      [String.raw`C:\Users\Actor\Documents\notes.txt`],
    ),
    /current attachment action/,
  )

  const requests = writes.map((wireRequest) => JSON.parse(wireRequest))
  assert.deepEqual(
    requests.map((request) => request.method),
    ['attachment.list', 'attachment.list'],
  )
  for (const [request, scope] of [
    [requests[0], chatScope],
    [requests[1], projectScope],
  ]) {
    backend.handleProtocolLine(JSON.stringify({
      type: 'response',
      protocol: fixtures.protocol,
      id: request.id,
      ok: true,
      result: {
        scope,
        attachments: [],
        maxFileBytes: 16_777_216,
        maxFileCount: 10,
      },
    }))
  }

  assert.deepEqual((await chatListing).scope, chatScope)
  assert.deepEqual((await projectListing).scope, projectScope)
})

test('Backend rejects an Attachment response for another scope', async () => {
  const writes = []
  let killCount = 0
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => {
      killCount += 1
      return true
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['attachment.management'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const listing = backend.listAttachments({
    kind: 'chat',
    id: 'chat_fixture',
  })
  const request = JSON.parse(writes.at(-1))
  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: request.id,
    ok: true,
    result: {
      scope: { kind: 'chat', id: 'chat_second' },
      attachments: [],
      maxFileBytes: 16_777_216,
      maxFileCount: 10,
    },
  }))

  await assert.rejects(listing, /does not match its requested scope/)
  assert.equal(killCount, 1)
  assert.equal(backend.getSnapshot().status, 'error')
})

test('Backend rejects a pending Settings action on a malformed result', async () => {
  const writes = []
  let killCount = 0
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => {
      killCount += 1
      return true
    },
  }
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: ['settings.management'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  const getting = backend.getSettings()
  const request = JSON.parse(writes.at(-1))
  const response = settingsStateResponse()
  const secret = 'must-not-appear-in-errors'
  response.id = request.id
  response.result.settings.apiKey = secret
  backend.handleProtocolLine(JSON.stringify(response))

  await assert.rejects(
    getting,
    (error) => (
      error instanceof Error
      && /Invalid Backend protocol frame/.test(error.message)
      && !error.message.includes(secret)
    ),
  )
  assert.equal(killCount, 1)
  assert.equal(backend.getSnapshot().status, 'error')
})

test('Backend keeps an initialized child available for Settings repair', async () => {
  const writes = []
  let killCount = 0
  const backend = new BackendProcess('.', () => undefined)
  const child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => {
      killCount += 1
      return true
    },
  }
  backend.child = child
  backend.snapshot = {
    revision: 1,
    status: 'initializing',
    capabilities: ['settings.management', 'voice.settings'],
    models: [],
  }
  backend.initializeRequestId = 'initialize-repair'
  backend.pendingRequests.set('initialize-repair', {
    method: 'initialize',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
  })

  backend.handleProtocolLine(JSON.stringify({
    type: 'response',
    protocol: fixtures.protocol,
    id: 'initialize-repair',
    ok: false,
    error: {
      code: 'backend.initialization_failed',
      message: 'Saved settings need repair.',
      retryable: false,
    },
  }))

  assert.equal(backend.child, child)
  assert.equal(killCount, 0)
  assert.equal(backend.getSnapshot().status, 'error')
  assert.deepEqual(
    backend.getSnapshot().capabilities,
    ['settings.management', 'voice.settings'],
  )

  const getting = backend.getSettings()
  const request = JSON.parse(writes.at(-1))
  assert.equal(request.method, 'settings.get')
  const response = settingsStateResponse()
  response.id = request.id
  backend.handleProtocolLine(JSON.stringify(response))

  assert.equal((await getting).revision, response.result.revision)
  assert.equal(backend.child, child)

  const gettingVoice = backend.getVoiceSettings()
  const voiceRequest = JSON.parse(writes.at(-1))
  assert.equal(voiceRequest.method, 'voice.settings.get')
  const voiceResponse = voiceSettingsStateResponse()
  voiceResponse.id = voiceRequest.id
  backend.handleProtocolLine(JSON.stringify(voiceResponse))

  assert.equal((await gettingVoice).revision, voiceResponse.result.revision)
  assert.equal(backend.child, child)
})

test('Backend restart settles only after ready and rejects on error', async () => {
  const backend = new BackendProcess('.', () => undefined)
  backend.stop = async () => undefined
  backend.start = () => {
    backend.updateSnapshot({ status: 'initializing' })
  }

  let settled = false
  const restarting = backend.restart()
  restarting.then(
    () => { settled = true },
    () => { settled = true },
  )
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(settled, false)

  backend.updateSnapshot({
    status: 'ready',
    modelName: 'qwen3.5:9b',
    models: ['qwen3.5:9b'],
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  })
  assert.equal((await restarting).status, 'ready')
  assert.equal(settled, true)

  const failingBackend = new BackendProcess('.', () => undefined)
  failingBackend.stop = async () => undefined
  failingBackend.start = () => {
    failingBackend.updateSnapshot({ status: 'initializing' })
  }
  const failingRestart = failingBackend.restart()
  const rejected = assert.rejects(
    failingRestart,
    /Could not initialize saved settings/,
  )
  await new Promise((resolve) => setImmediate(resolve))
  failingBackend.updateSnapshot({
    status: 'error',
    error: 'Could not initialize saved settings.',
  })
  await rejected
})

test('Backend restart restores the previous Chat after a failure', async () => {
  const writes = []
  const backend = new BackendProcess('.', () => undefined)
  backend.child = {
    stdin: {
      writable: true,
      write: (value) => writes.push(value),
    },
    kill: () => true,
  }
  backend.updateSnapshot({
    status: 'ready',
    capabilities: ['chat.sessions'],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Original Chat',
  })
  backend.protocolFailure('Temporary connection failure.')
  assert.equal(backend.getSnapshot().status, 'error')
  assert.equal(backend.getSnapshot().chatId, undefined)
  assert.equal(backend.getSnapshot().modelName, undefined)

  backend.stop = async () => undefined
  backend.start = () => {
    backend.updateSnapshot({ status: 'initializing' })
  }

  const restarting = backend.restart()
  await new Promise((resolve) => setImmediate(resolve))
  backend.updateSnapshot({
    status: 'ready',
    modelName: 'qwen3.5:9b',
    models: ['qwen3.5:9b'],
    chatId: 'chat_second',
    chatTitle: 'Backend Default',
  })
  await new Promise((resolve) => setImmediate(resolve))

  const openRequest = JSON.parse(writes.at(-1))
  assert.equal(openRequest.method, 'chat.open')
  assert.deepEqual(openRequest.params, { chatId: 'chat_fixture' })

  const chatSample = fixtures.validServerMessages.find(
    (candidate) => candidate.name === 'chat state response',
  )
  assert.ok(chatSample)
  const openResponse = structuredClone(chatSample.message)
  openResponse.id = openRequest.id
  backend.handleProtocolLine(JSON.stringify(openResponse))

  const snapshot = await restarting
  assert.equal(snapshot.status, 'ready')
  assert.equal(snapshot.chatId, 'chat_fixture')
  assert.equal(snapshot.chatTitle, 'Elysia Chat')
})

test('Backend rejects a concurrent restart before stopping twice', async () => {
  const backend = new BackendProcess('.', () => undefined)
  const firstRestartChild = { identity: 'first-restart-child' }
  backend.child = firstRestartChild

  let stopCalls = 0
  let releaseInitialStop
  const initialStop = new Promise((resolve) => {
    releaseInitialStop = resolve
  })
  backend.stop = async () => {
    stopCalls += 1
    await initialStop
  }
  backend.start = () => {
    backend.updateSnapshot({ status: 'initializing' })
  }

  const firstRestart = backend.restart()
  await assert.rejects(
    backend.restart(),
    /A Backend restart is already pending/,
  )
  assert.equal(stopCalls, 1)
  assert.equal(backend.child, firstRestartChild)

  releaseInitialStop()
  await new Promise((resolve) => setImmediate(resolve))
  backend.updateSnapshot({
    status: 'ready',
    modelName: 'qwen3.5:9b',
    models: ['qwen3.5:9b'],
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  })
  assert.equal((await firstRestart).status, 'ready')
})

test('Backend explicit stop promptly rejects an in-flight restart', async () => {
  const backend = new BackendProcess('.', () => undefined)
  backend.stop = async () => undefined
  backend.start = () => {
    backend.updateSnapshot({ status: 'initializing' })
  }

  const restarting = backend.restart()
  const outcomePromise = restarting.then(
    () => ({ kind: 'resolved' }),
    (error) => ({ kind: 'rejected', error }),
  )
  await new Promise((resolve) => setImmediate(resolve))
  assert.notEqual(backend.restartCompletion, null)

  delete backend.stop
  await backend.stop()
  const outcome = await Promise.race([
    outcomePromise,
    new Promise((resolve) => {
      setTimeout(() => resolve({ kind: 'timeout' }), 100)
    }),
  ])
  if (outcome.kind === 'timeout') {
    backend.rejectRestartCompletion('Test cleanup after restart timeout.')
  }

  assert.equal(outcome.kind, 'rejected')
  assert.match(
    outcome.error.message,
    /stopped before its restart completed/,
  )
  assert.equal(backend.getSnapshot().status, 'stopped')
})

test('Backend stop rejects all pending renderer actions', async () => {
  const backend = new BackendProcess('.', () => undefined)
  const child = new EventEmitter()
  child.stdin = {
    writable: true,
    write: () => true,
  }
  child.kill = () => true
  backend.child = child
  backend.snapshot = {
    revision: 1,
    status: 'ready',
    capabilities: [
      'chat.sessions',
      'project.management',
      'voice.capture',
    ],
    models: ['qwen3.5:9b'],
    modelName: 'qwen3.5:9b',
    chatId: 'chat_fixture',
    chatTitle: 'Elysia Chat',
  }

  let rejectChat
  const chatPromise = new Promise((_resolve, reject) => {
    rejectChat = reject
  })
  let rejectProject
  const projectPromise = new Promise((_resolve, reject) => {
    rejectProject = reject
  })
  backend.pendingRequests.set('chat-list-stop', {
    method: 'chat.list',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    rejectChatState: rejectChat,
  })
  backend.pendingRequests.set('project-list-stop', {
    method: 'project.list',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    rejectProjectState: rejectProject,
  })
  let rejectSettings
  const settingsPromise = new Promise((_resolve, reject) => {
    rejectSettings = reject
  })
  backend.pendingRequests.set('settings-get-stop', {
    method: 'settings.get',
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    rejectSettingsState: rejectSettings,
  })
  let rejectAttachment
  const attachmentPromise = new Promise((_resolve, reject) => {
    rejectAttachment = reject
  })
  backend.pendingRequests.set('attachment-list-stop', {
    method: 'attachment.list',
    attachmentScope: { kind: 'chat', id: 'chat_fixture' },
    nextSequence: 0,
    streamCompleted: false,
    streamedReply: '',
    streamedLength: 0,
    rejectAttachmentState: rejectAttachment,
  })
  const voicePromise = backend.submitVoiceCapture(
    voiceCaptureRequest().params,
  )
  const voicePending = [...backend.pendingRequests.values()].find(
    (pending) => pending.method === 'voice.capture.complete',
  )
  assert.ok(voicePending.timeout)

  const stopping = backend.stop()
  await assert.rejects(chatPromise, /stopping before the action completed/)
  await assert.rejects(projectPromise, /stopping before the action completed/)
  await assert.rejects(settingsPromise, /stopping before the action completed/)
  await assert.rejects(
    attachmentPromise,
    /stopping before the action completed/,
  )
  await assert.rejects(voicePromise, /stopping before the action completed/)
  assert.equal(voicePending.timeout, undefined)
  child.emit('close', 0, null)
  await stopping
})

test('Backend replaces child stderr with a fixed renderer-safe diagnostic', async () => {
  const projectRoot = await mkdtemp(path.join(tmpdir(), 'elysia-stderr-test-'))
  const bridgePath = path.join(projectRoot, 'desktop_backend.py')
  const previousPython = process.env.ELYSIA_PYTHON
  let resolveError
  const reachedError = new Promise((resolve) => {
    resolveError = resolve
  })
  const backend = new BackendProcess(projectRoot, (event) => {
    if (event.type === 'snapshot' && event.snapshot.status === 'error') {
      resolveError(event.snapshot)
    }
  })

  try {
    // Node accepts an arbitrary main-file extension. This tiny child avoids a
    // Python dependency while exercising the real spawn/stderr/exit boundary.
    await writeFile(
      bridgePath,
      "process.stdin.resume(); process.stderr.write('D:/private/model native crash\\n'); setTimeout(() => process.exit(1), 25);\n",
      'utf8',
    )
    process.env.ELYSIA_PYTHON = process.execPath
    backend.start()
    let diagnosticTimeout
    const timeoutOutcome = new Promise((resolve) => {
      diagnosticTimeout = setTimeout(
        () => resolve({ status: 'timeout' }),
        2_000,
      )
    })
    const outcome = await Promise.race([
      reachedError,
      timeoutOutcome,
    ])
    clearTimeout(diagnosticTimeout)

    assert.equal(outcome.status, 'error')
    assert.equal(
      outcome.error,
      'Python Backend reported an internal diagnostic.',
    )
    assert.doesNotMatch(outcome.error, /private|model|native crash/u)
  } finally {
    if (previousPython === undefined) {
      delete process.env.ELYSIA_PYTHON
    } else {
      process.env.ELYSIA_PYTHON = previousPython
    }
    await backend.stop()
    await rm(projectRoot, { recursive: true, force: true })
  }
})

test('Backend replaces child process errors with a fixed diagnostic', async () => {
  const projectRoot = await mkdtemp(path.join(tmpdir(), 'elysia-spawn-test-'))
  const bridgePath = path.join(projectRoot, 'desktop_backend.py')
  const previousPython = process.env.ELYSIA_PYTHON
  const backend = new BackendProcess(projectRoot, () => undefined)
  let child

  try {
    // A live disposable child lets the test trigger the installed error
    // listener without relying on platform-specific spawn failure wording.
    await writeFile(
      bridgePath,
      'process.stdin.resume();\n',
      'utf8',
    )
    process.env.ELYSIA_PYTHON = process.execPath
    backend.start()
    child = backend.child
    assert.ok(child)
    assert.ok(child.stdio[3])
    assert.equal(backend.speechAudioInput, child.stdio[3])
    const exited = new Promise((resolve) => child.once('exit', resolve))

    child.emit(
      'error',
      new Error('spawn D:/private/python.exe EACCES'),
    )

    assert.equal(backend.getSnapshot().status, 'error')
    assert.equal(
      backend.getSnapshot().error,
      'Python Backend process could not be started.',
    )
    assert.doesNotMatch(
      backend.getSnapshot().error,
      /private|python\.exe|EACCES/u,
    )
    child.kill()
    await exited
    child = undefined
  } finally {
    if (previousPython === undefined) {
      delete process.env.ELYSIA_PYTHON
    } else {
      process.env.ELYSIA_PYTHON = previousPython
    }
    if (child !== undefined) {
      child.kill()
    }
    await backend.stop()
    await rm(projectRoot, { recursive: true, force: true })
  }
})

test('Backend replaces child stdin errors with a fixed diagnostic', async () => {
  const projectRoot = await mkdtemp(path.join(tmpdir(), 'elysia-stdin-test-'))
  const bridgePath = path.join(projectRoot, 'desktop_backend.py')
  const previousPython = process.env.ELYSIA_PYTHON
  const backend = new BackendProcess(projectRoot, () => undefined)
  let child

  try {
    await writeFile(
      bridgePath,
      'process.stdin.resume();\n',
      'utf8',
    )
    process.env.ELYSIA_PYTHON = process.execPath
    backend.start()
    child = backend.child
    assert.ok(child)
    const exited = new Promise((resolve) => child.once('exit', resolve))

    child.stdin.emit(
      'error',
      new Error('write D:/private/python-pipe EPIPE'),
    )

    assert.equal(backend.getSnapshot().status, 'error')
    assert.equal(
      backend.getSnapshot().error,
      'Python Backend input failed.',
    )
    assert.doesNotMatch(
      backend.getSnapshot().error,
      /private|python-pipe|EPIPE/u,
    )
    await exited
    child = undefined
  } finally {
    if (previousPython === undefined) {
      delete process.env.ELYSIA_PYTHON
    } else {
      process.env.ELYSIA_PYTHON = previousPython
    }
    if (child !== undefined) {
      child.kill()
    }
    await backend.stop()
    await rm(projectRoot, { recursive: true, force: true })
  }
})

test('Backend rejects child stdout truncated before its newline', async () => {
  const projectRoot = await mkdtemp(path.join(tmpdir(), 'elysia-stdout-test-'))
  const bridgePath = path.join(projectRoot, 'desktop_backend.py')
  const previousPython = process.env.ELYSIA_PYTHON
  let resolveError
  const reachedError = new Promise((resolve) => {
    resolveError = resolve
  })
  const backend = new BackendProcess(projectRoot, (event) => {
    if (event.type === 'snapshot' && event.snapshot.status === 'error') {
      resolveError(event.snapshot)
    }
  })

  try {
    await writeFile(
      bridgePath,
      "process.stdin.resume(); process.stdout.write('{\\\"type\\\":\\\"response\\\"}', () => process.exit(0));\n",
      'utf8',
    )
    process.env.ELYSIA_PYTHON = process.execPath
    backend.start()
    let diagnosticTimeout
    const timeoutOutcome = new Promise((resolve) => {
      diagnosticTimeout = setTimeout(
        () => resolve({ status: 'timeout' }),
        2_000,
      )
    })
    const outcome = await Promise.race([reachedError, timeoutOutcome])
    clearTimeout(diagnosticTimeout)

    assert.equal(outcome.status, 'error')
    assert.equal(
      outcome.error,
      'Invalid Backend protocol frame: Backend output ended before its newline delimiter.',
    )
  } finally {
    if (previousPython === undefined) {
      delete process.env.ELYSIA_PYTHON
    } else {
      process.env.ELYSIA_PYTHON = previousPython
    }
    await backend.stop()
    await rm(projectRoot, { recursive: true, force: true })
  }
})

test('renderer source policy accepts only the exact development document', () => {
  const policy = {
    appPath: '/application',
    developmentUrl: 'http://localhost:5173',
    isPackaged: false,
    platform: 'linux',
  }

  assert.equal(isTrustedRendererUrl('http://localhost:5173/', policy), true)
  assert.equal(isTrustedRendererUrl('http://127.0.0.1:5173/', policy), false)
  assert.equal(isTrustedRendererUrl('http://localhost:5173/iframe', policy), false)
  assert.equal(isTrustedRendererUrl('https://localhost:5173/', policy), false)
})

test('renderer source policy accepts only the packaged index file', () => {
  const appPath = path.resolve('fixture-app')
  const policy = {
    appPath,
    developmentUrl: 'http://localhost:5173',
    isPackaged: true,
    platform: process.platform,
  }
  const indexUrl = pathToFileURL(
    path.join(appPath, 'dist', 'index.html'),
  ).href
  const otherUrl = pathToFileURL(
    path.join(appPath, 'dist', 'other.html'),
  ).href

  assert.equal(isTrustedRendererUrl(indexUrl, policy), true)
  assert.equal(isTrustedRendererUrl(otherUrl, policy), false)
})

test('audio permission policy allows only trusted microphone access', () => {
  const trusted = {
    isMainFrame: true,
    isMainWindow: true,
    requestingUrlTrusted: true,
    currentUrlTrusted: true,
  }

  assert.equal(allowAudioPermissionCheck('media', 'audio', trusted), true)
  assert.equal(
    allowAudioPermissionRequest('media', ['audio'], trusted),
    true,
  )
  assert.equal(allowAudioPermissionCheck('media', 'video', trusted), false)
  assert.equal(
    allowAudioPermissionRequest('media', ['audio', 'video'], trusted),
    false,
  )
  assert.equal(
    allowAudioPermissionRequest('media', ['video'], trusted),
    false,
  )
})

test('audio permission policy narrowly allows trusted speaker selection', () => {
  const trusted = {
    isMainFrame: true,
    isMainWindow: true,
    requestingUrlTrusted: true,
    currentUrlTrusted: true,
  }

  assert.equal(
    allowAudioPermissionRequest('speaker-selection', undefined, trusted),
    true,
  )
  assert.equal(
    allowAudioPermissionRequest('notifications', undefined, trusted),
    false,
  )
  for (const field of Object.keys(trusted)) {
    assert.equal(
      allowAudioPermissionRequest('speaker-selection', undefined, {
        ...trusted,
        [field]: false,
      }),
      false,
    )
  }
})

test('external link policy allows credential-free HTTP(S) URLs only', () => {
  assert.equal(
    parseSafeExternalUrl('https://example.com/docs?q=elysia#message'),
    'https://example.com/docs?q=elysia#message',
  )
  assert.throws(() => parseSafeExternalUrl('javascript:alert(1)'))
  assert.throws(() => parseSafeExternalUrl('https://user@example.com/'))
  assert.throws(() => parseSafeExternalUrl('https://example.com\0.invalid/'))
  assert.throws(() => parseSafeExternalUrl(' https://example.com/'))
})
