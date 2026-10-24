/** @fileoverview Verify saved-name validation and the authenticated bridge without Ollama or user-data writes. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { BackendProcess } from '../dist-electron/backend-process.js'
import { parseUpdateUserProfileRequest, parseUserProfileState } from '../dist-electron/protocol.js'

const protocol = { name: 'elysia.desktop', version: 1 }
const fixtures = JSON.parse(await readFile(new URL('../../desktop_protocol/fixtures/v1.samples.json', import.meta.url), 'utf8'))

function fixture(status = 'error') {
  const writes = []
  const backend = new BackendProcess('.', () => {})
  const child = new EventEmitter()
  child.stdin = { writable: true, write: wire => writes.push(JSON.parse(wire)) }
  child.kill = () => true
  backend.child = child
  backend.snapshot = { revision: 1, status, capabilities: ['settings.management'], models: [] }
  return { backend, writes }
}

function respond(backend, request, response) {
  backend.handleProtocolLine(JSON.stringify({ type: 'response', protocol, id: request.id, ...response }))
}

test('new names are bounded by Unicode code points while legacy compare values remain readable', () => {
  assert.equal(parseUpdateUserProfileRequest({ expectedUserName: '', userName: '🌟'.repeat(80) }).userName.length, 160)
  assert.deepEqual(parseUserProfileState({ kind: 'profile', userName: '' }), { kind: 'profile', userName: '' })
  for (const userName of ['', ' Name', 'Name ', 'a\nb', 'a\0b', '\ud800', 'x'.repeat(81)]) {
    assert.throws(() => parseUpdateUserProfileRequest({ expectedUserName: 'User', userName }))
  }
  assert.throws(() => parseUpdateUserProfileRequest({ expectedUserName: 'User', userName: 'Name', project: 'private' }))
  assert.throws(() => parseUserProfileState({ kind: 'profile', userName: 'Name', languages: [] }))
})

test('name read and save work after model initialization failed and deliver only narrow state', async () => {
  const { backend, writes } = fixture()
  const read = backend.getProfile()
  assert.equal(writes[0].method, 'profile.get')
  respond(backend, writes[0], { ok: true, result: { kind: 'profile', userName: 'Ying' } })
  assert.deepEqual(await read, { kind: 'profile', userName: 'Ying' })
  const save = backend.updateProfile({ expectedUserName: 'Ying', userName: '小星' })
  assert.deepEqual(writes[1].params, { expectedUserName: 'Ying', userName: '小星' })
  respond(backend, writes[1], { ok: true, result: { kind: 'profile', userName: '小星' } })
  assert.equal((await save).userName, '小星')
})

test('conflict preserves canonical state and a second simultaneous save never reaches Python', async () => {
  const { backend, writes } = fixture()
  const first = backend.updateProfile({ expectedUserName: 'User', userName: 'Name' })
  await assert.rejects(backend.updateProfile({ expectedUserName: 'User', userName: 'Second' }), /Wait/)
  assert.equal(writes.length, 1)
  const failure = assert.rejects(first, /changed elsewhere/)
  respond(backend, writes[0], { ok: false, error: { code: 'profile.conflict', message: 'Your name changed elsewhere. Reload it before saving.', retryable: false } })
  await failure
  const read = backend.getProfile()
  respond(backend, writes[1], { ok: true, result: { kind: 'profile', userName: 'Actual' } })
  assert.equal((await read).userName, 'Actual')
})

test('invalid edits and stopped backend reject without creating pending requests', async () => {
  const { backend, writes } = fixture()
  await assert.rejects(backend.updateProfile({ expectedUserName: 'User', userName: '\n' }))
  assert.equal(writes.length, 0)
  assert.equal(backend.pendingRequests.size, 0)
  backend.snapshot.status = 'stopped'
  await assert.rejects(backend.getProfile(), /not available/)
})

test('backend failure rejects pending profile operations instead of leaving Settings saving', async () => {
  const { backend } = fixture()
  const read = backend.getProfile()
  const rejected = assert.rejects(read, /restarting/)
  backend.rejectPendingActionPromises('Backend restarting')
  await rejected
})

test('initialization failure retains queued profile, global and voice preference requests', async () => {
  const { backend, writes } = fixture('initializing')
  backend.snapshot.capabilities.push('voice.settings')
  let killCount = 0
  const child = backend.child
  child.kill = () => { killCount += 1; return true }
  backend.initializeRequestId = 'initialize-profile'
  backend.pendingRequests.set('initialize-profile', {
    method: 'initialize', nextSequence: 0, streamCompleted: false, streamedReply: '', streamedLength: 0,
  })
  // A first-launch Settings view can send these while Python is discovering
  // Ollama. Their valid responses must survive the earlier initialize error.
  const profile = backend.getProfile()
  const settings = backend.getSettings()
  const voice = backend.getVoiceSettings()
  respond(backend, { id: 'initialize-profile' }, {
    ok: false, error: { code: 'backend.ollama_unavailable', message: 'Local Ollama is unavailable.', retryable: true },
  })
  assert.equal(backend.getSnapshot().status, 'error')
  assert.equal(backend.getSnapshot().initializationErrorCode, 'backend.ollama_unavailable')
  assert.equal(backend.pendingRequests.size, 3)
  respond(backend, writes[0], { ok: true, result: { kind: 'profile', userName: 'New name' } })
  const settingsResult = fixtures.validServerMessages.find(sample => sample.name === 'settings state response').message.result
  const voiceResult = fixtures.validServerMessages.find(sample => sample.name === 'voice settings state response').message.result
  respond(backend, writes[1], { ok: true, result: settingsResult })
  respond(backend, writes[2], { ok: true, result: voiceResult })
  assert.equal((await profile).userName, 'New name')
  assert.equal((await settings).revision, settingsResult.revision)
  assert.equal((await voice).revision, voiceResult.revision)
  assert.equal(backend.pendingRequests.size, 0)
  assert.equal(backend.child, child)
  assert.equal(killCount, 0)
  const save = backend.updateProfile({ expectedUserName: 'New name', userName: '小星' })
  respond(backend, writes[3], { ok: true, result: { kind: 'profile', userName: '小星' } })
  assert.equal((await save).userName, '小星')
})
