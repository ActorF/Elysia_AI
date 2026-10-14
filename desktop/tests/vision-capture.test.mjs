/** Verify exact one-use capture ownership, stale-source refusal, normalized regions, expiry, and physical callback drain using inert native providers. */
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const url = process.env.ELYSIA_VISION_CAPTURE_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_VISION_CAPTURE_TEST_MODULE).href : new URL('../dist-electron/vision-capture.js', import.meta.url).href
const { VisionCaptureController, VisionCaptureError } = await import(url)
const protocol = await import(new URL('./vision-capture-protocol.js', url))
const original = { chatId: 'chat_capture', projectId: null, mode: 'chat', archived: false, ready: true, busy: false }
const display = { nativeId: 'screen:1:0', identity: 'a'.repeat(64), kind: 'display', label: 'Display 1', width: 1000, height: 500 }
const window = { nativeId: 'window:2:0', identity: 'b'.repeat(64), kind: 'window', label: 'Synthetic window', width: null, height: null }
const errorCode = code => error => error instanceof VisionCaptureError && error.code === code

function fixture() {
  const state = { kind: 'vision.state', owner: { chatId: original.chatId, projectId: null }, revision: 0, busy: false, retention: 'session', items: [] }
  const current = { ...original }, seen = []
  let now = 100, sources = [display, window], enumerate = 0, held = null
  const dependencies = {
    getOwner: () => current,
    listSources: async () => { enumerate += 1; return sources.map(item => ({ ...item })) },
    captureFrame: async (selected, region, signal, guard) => {
      seen.push({ selected, region, signal, guard })
      if (held !== null) await held
      return { pngBytes: Buffer.from('synthetic'), width: 100, height: 50 }
    },
    getState: () => structuredClone(state),
    importFrame: (chatId, frame, source) => { seen.push({ imported: { chatId, frame, source } }); return { cancelled: false, state: structuredClone(state) } },
  }
  const controller = new VisionCaptureController(dependencies, () => now, () => new Date('2026-01-01T00:00:00Z'))
  return { controller, current, seen, dependencies, get enumerate() { return enumerate },
    set now(value) { now = value }, set sources(value) { sources = value }, set held(value) { held = value } }
}
async function approval(f, region = null, kind = 'display') {
  const list = await f.controller.listSources(original.chatId)
  return f.controller.prepare({ chatId: original.chatId, selectionId: list.sources.find(item => item.kind === kind).selectionId, region })
}
async function until(predicate) {
  for (let index = 0; index < 100; index += 1) { if (predicate()) return; await new Promise(resolve => setImmediate(resolve)) }
  assert.fail('Native callback did not start.')
}

test('Source listing and preparing are metadata-only; native IDs stay private and one explicit grant imports one cropped frame', async () => {
  const f = fixture(), region = { x: 0.25, y: 0.25, width: 0.5, height: 0.5 }
  const pending = await approval(f, region)
  assert.equal(f.seen.length, 0); assert.equal(JSON.stringify(pending).includes('screen:1:0'), false)
  assert.deepEqual(pending.region, region)
  const result = await f.controller.capture({ chatId: original.chatId, approvalId: pending.approvalId, granted: true })
  assert.equal(result.cancelled, false); assert.equal(f.seen.length, 2)
  assert.equal(f.enumerate, 3)
  assert.deepEqual(f.seen[0].region, region); assert.equal(f.seen[0].selected.nativeId, display.nativeId)
  assert.deepEqual(f.seen[1].imported.source, { kind: 'region', label: display.label })
  await assert.rejects(f.controller.capture({ chatId: original.chatId, approvalId: pending.approvalId, granted: true }), errorCode('permission_denied'))
})

test('Decline and a newer preview consume or supersede the old token without calling the frame provider', async () => {
  const f = fixture(), first = await approval(f)
  const next = f.controller.prepare({ chatId: original.chatId, selectionId: first.source.selectionId, region: null })
  await assert.rejects(f.controller.capture({ chatId: original.chatId, approvalId: first.approvalId, granted: true }), errorCode('permission_denied'))
  assert.equal((await f.controller.capture({ chatId: original.chatId, approvalId: next.approvalId, granted: false })).cancelled, true)
  assert.equal(f.seen.length, 0)
  await assert.rejects(f.controller.capture({ chatId: original.chatId, approvalId: next.approvalId, granted: true }), errorCode('permission_denied'))
})

test('Stale source identity before capture or after a returned frame discards pixels instead of publishing changed provenance', async () => {
  const f = fixture(), first = await approval(f)
  f.sources = [{ ...display, identity: 'c'.repeat(64) }, window]
  await assert.rejects(f.controller.capture({ chatId: original.chatId, approvalId: first.approvalId, granted: true }), errorCode('owner_changed'))
  assert.equal(f.seen.length, 0)
  f.sources = [display, window]
  const next = await approval(f)
  f.dependencies.captureFrame = async () => { f.sources = [window]; return { pngBytes: Buffer.from('old'), width: 1, height: 1 } }
  await assert.rejects(f.controller.capture({ chatId: original.chatId, approvalId: next.approvalId, granted: true }), errorCode('owner_changed'))
  assert.equal(f.seen.length, 0)
})

test('Canonical Chat, Project, mode, archive and shutdown changes cannot revive a prior capture grant', async () => {
  for (const change of [{ chatId: 'chat_other' }, { projectId: 'project_other' }, { mode: 'work' }, { archived: true }, { ready: false }]) {
    const f = fixture(), pending = await approval(f)
    Object.assign(f.current, change)
    await assert.rejects(f.controller.capture({ chatId: original.chatId, approvalId: pending.approvalId, granted: true }), error => error instanceof VisionCaptureError)
    assert.equal(f.seen.length, 0)
  }
  const f = fixture(), pending = await approval(f)
  f.controller.shutdown()
  await assert.rejects(f.controller.capture({ chatId: original.chatId, approvalId: pending.approvalId, granted: true }), errorCode('unavailable'))
})

test('Cancel and navigation revoke publication immediately while the blocked callback still owns physical capacity', async () => {
  for (const cancelled of [true, false]) {
    const f = fixture(), pending = await approval(f)
    let release
    f.held = new Promise(resolve => { release = resolve })
    const running = f.controller.capture({ chatId: original.chatId, approvalId: pending.approvalId, granted: true })
    const refused = assert.rejects(running, errorCode(cancelled ? 'cancelled' : 'owner_changed'))
    await until(() => f.seen.length === 1)
    if (cancelled) assert.equal(f.controller.cancel(original.chatId).busy, true)
    else { f.current.projectId = 'project_other'; f.controller.synchronize() }
    assert.equal(f.seen[0].signal.aborted, true); assert.equal(f.seen[0].guard(), false)
    assert.equal(f.controller.hasActiveWork(), true)
    await assert.rejects(f.controller.listSources(original.chatId), error => error instanceof VisionCaptureError)
    release(); await refused
    assert.equal(f.controller.hasActiveWork(), false); assert.equal(f.seen.length, 1)
  }
})

test('Monotonic expiry and a backwards clock reject stale grants without reading pixels', async () => {
  const f = fixture(), pending = await approval(f)
  f.now = 60100
  await assert.rejects(f.controller.capture({ chatId: original.chatId, approvalId: pending.approvalId, granted: true }), errorCode('timeout'))
  assert.equal(f.seen.length, 0)
  const g = fixture(), next = await approval(g)
  g.now = 99
  await assert.rejects(g.controller.capture({ chatId: original.chatId, approvalId: next.approvalId, granted: true }), errorCode('unavailable'))
})

test('Metadata delay counts against the capture admission budget and cannot produce a native frame after timeout', async () => {
  const f = fixture(), pending = await approval(f)
  f.dependencies.listSources = async () => { f.now = 20100; return [display, window] }
  await assert.rejects(f.controller.capture({ chatId: original.chatId, approvalId: pending.approvalId, granted: true }), errorCode('timeout'))
  assert.equal(f.seen.length, 0)
})

test('Region/window, dimensions, extra fields and native-path injection stay outside the closed capture protocol', async () => {
  for (const region of [{ x: -0.1, y: 0, width: 1, height: 1 }, { x: 0, y: 0, width: 0, height: 1 },
    { x: 0, y: 0, width: Infinity, height: 1 }, { x: 0.5, y: 0, width: 0.6, height: 1 },
    { x: 0, y: 0, width: 1, height: 1, nativeId: 'screen:1:0' }]) {
    assert.throws(() => protocol.parseVisionCaptureRegion(region), TypeError)
  }
  const f = fixture(), pending = await approval(f, null, 'window')
  assert.throws(() => f.controller.prepare({ chatId: original.chatId, selectionId: pending.source.selectionId,
    region: { x: 0, y: 0, width: 1, height: 1 } }), errorCode('invalid_request'))
  assert.throws(() => protocol.parseVisionCaptureConfirmRequest({ chatId: original.chatId, approvalId: pending.approvalId,
    granted: true, nativeId: display.nativeId }), TypeError)
  assert.throws(() => protocol.parseVisionCaptureSource({ ...pending.source, label: 'C:\\private\\window' }), TypeError)
  f.sources = [{ ...display, width: 8192, height: 8192 }]
  const list = await f.controller.listSources(original.chatId)
  assert.throws(() => f.controller.prepare({ chatId: original.chatId, selectionId: list.sources[0].selectionId, region: null }), errorCode('limit_exceeded'))
})
