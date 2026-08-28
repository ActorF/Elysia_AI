import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'
import { pathToFileURL } from 'node:url'

import { BackendProcess } from '../dist-electron/backend-process.js'
import { isTrustedRendererUrl } from '../dist-electron/renderer-source.js'
import {
  PROTOCOL_NAME,
  PROTOCOL_VERSION,
  MAX_PROTOCOL_FRAME_BYTES,
  ProtocolValidationError,
  createRequest,
  hasNonBlankCodePoint,
  parseChatResult,
  parseClientRequest,
  parseHandshakeResult,
  parseInitializeResult,
  parseChatStateResult,
  parseProjectStateResult,
  parseServerMessage,
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

function createPendingChat() {
  const events = []
  const backend = new BackendProcess('.', (event) => events.push(event))
  backend.pendingRequests.set('chat-state-1', {
    method: 'chat.stream',
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

test('Backend stop rejects pending Chat and Project actions', async () => {
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
    capabilities: ['chat.sessions', 'project.management'],
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

  const stopping = backend.stop()
  await assert.rejects(chatPromise, /stopping before the action completed/)
  await assert.rejects(projectPromise, /stopping before the action completed/)
  child.emit('exit', 0, null)
  await stopping
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
