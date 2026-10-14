/** Verify exact single-use Issue approvals, secret isolation, native confirmation and truthful physically drained write outcomes. */
import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { InternetPolicyService } from '../dist-electron/internet-policy.js'
import { InternetWriteController } from '../dist-electron/internet-write.js'
import { parseConfirmGithubIssueRequest, parseGithubIssueApproval, parseGithubIssueWriteResult,
  parseInternetWriteStatus, parsePrepareGithubIssueRequest } from '../dist-electron/internet-write-protocol.js'

const at = Date.parse('2026-10-14T20:00:00.000Z')
const request = () => ({ chatId: 'chat_a', owner: 'octocat', repo: 'Hello-World', title: 'Exact proposed title', body: 'Line 1\nLine 2' })
const created = () => ({ outcome: 'created', url: 'https://github.com/octocat/Hello-World/issues/7', number: 7 })
const outcome = value => ({ outcome: value, url: null, number: null })
const code = expected => error => error.code === expected
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function fixture(t, options = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-internet-write-'))
  const file = path.join(directory, 'policy.json'), policy = new InternetPolicyService(file)
  if (options.enabled !== false) await policy.update({ expectedRevision: 0, enabled: true,
    allowedDomains: options.domains ?? ['api.github.com'], proxy: { mode: 'direct' }, timeoutMs: 15000 })
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', archived: false, ready: true, busy: false }
  const events = [], previews = [], calls = [], clock = { value: at }, secret = { value: 'any-test-secret' }
  let tokenReads = 0
  const controllerPolicy = options.denyApi ? new Proxy(policy, { get(target, key) {
    if (key === 'getState') return () => ({ ...target.getState(), allowedDomains: ['github.com'] })
    const value = Reflect.get(target, key)
    return typeof value === 'function' ? value.bind(target) : value
  } }) : policy
  const controller = new InternetWriteController({ policy: controllerPolicy, getOwner: () => owner, now: () => new Date(clock.value),
    readToken: () => { tokenReads += 1; return options.readToken === undefined ? secret.value : options.readToken() },
    onState: value => { events.push(value); options.onState?.(value) },
    confirm: async preview => { previews.push(preview); return options.confirm === undefined ? true : options.confirm(preview) },
    write: async (payload, token, signal) => {
      calls.push({ payload, token, signal })
      return options.write === undefined ? created() : options.write(payload, token, signal)
    } })
  t.after(async () => {
    controller.shutdown(); policy.shutdown()
    await Promise.all([controller.whenIdle(), policy.whenIdle()])
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    assert.match(path.basename(directory), /^elysia-internet-write-/u)
    await rm(directory, { recursive: true, force: true })
  })
  const confirm = receipt => controller.confirmGithubIssue({ chatId: 'chat_a', approvalId: receipt.id })
  const update = enabled => {
    const state = policy.getState()
    return policy.update({ expectedRevision: state.revision, enabled, allowedDomains: state.allowedDomains,
      proxy: state.proxy, timeoutMs: state.timeoutMs })
  }
  return { controller, policy, owner, events, previews, calls, clock, secret, file, confirm, update, tokenReads: () => tokenReads }
}

test('Closed requests reject arbitrary URLs, credentials, renderer approval flags and byte-oversized content', () => {
  assert.deepEqual(parsePrepareGithubIssueRequest(request()), request())
  assert.equal(parsePrepareGithubIssueRequest({ ...request(), title: '🌍'.repeat(64), body: '🌍'.repeat(1000) }).body.length, 2000)
  assert.equal(parsePrepareGithubIssueRequest({ ...request(), body: '' }).body, '')
  for (const changes of [{ token: 'secret' }, { projectId: 'project_other' }, { approved: true }, { owner: '-owner' }, { owner: 'a'.repeat(40) },
    { owner: 'https://github.com' }, { repo: '..' }, { repo: 'one/two' }, { repo: 'repo?token=secret' }, { title: '' }, { title: '  ' },
    { title: 'first\nsecond' }, { title: '🌍'.repeat(65) }, { body: '🌍'.repeat(1001) }, { body: 'bad\u0000' }, { body: '\ud800' }, { chatId: 'other' }]) {
    assert.throws(() => parsePrepareGithubIssueRequest({ ...request(), ...changes }), TypeError)
  }
  const confirm = { chatId: 'chat_a', approvalId: `internet_approval_${'a'.repeat(32)}` }
  assert.deepEqual(parseConfirmGithubIssueRequest(confirm), confirm)
  for (const changes of [{ title: 'different' }, { approved: true }, { token: 'secret' }, { approvalId: 'guessed' }]) {
    assert.throws(() => parseConfirmGithubIssueRequest({ ...confirm, ...changes }), TypeError)
  }
})

test('Public status and outcome parsers forbid credential additions, inconsistent successes and forged Issue links', () => {
  assert.deepEqual(parseInternetWriteStatus({ configured: true }), { configured: true })
  assert.throws(() => parseInternetWriteStatus({ configured: true, token: 'secret' }), TypeError)
  const success = { status: 'created', issueUrl: created().url, number: 7, error: null }
  assert.deepEqual(parseGithubIssueWriteResult(success), success)
  for (const changes of [{ number: 0 }, { number: 8 }, { error: 'network_failed' }, { issueUrl: 'https://evil.com/octocat/Hello-World/issues/7' },
    { issueUrl: 'https://github.com/octocat/Hello-World/issues/7?token=secret' }, { issueUrl: 'https://user:secret@github.com/octocat/Hello-World/issues/7' },
    { issueUrl: 'https://github.com/octocat/Hello-World/pull/7' }, { status: 'cancelled' }, { response: 'private server body' }]) {
    assert.throws(() => parseGithubIssueWriteResult({ ...success, ...changes }), TypeError)
  }
  for (const status of ['cancelled', 'outcome_unknown', 'failed']) {
    const value = { status, issueUrl: null, number: null, error: status === 'failed' ? 'rejected' : status }
    assert.deepEqual(parseGithubIssueWriteResult(value), value)
    assert.throws(() => parseGithubIssueWriteResult({ ...value, error: 'private server body' }), TypeError)
  }
})

test('Construction and status inspection do not read credentials early, open dialogs or perform writes', async t => {
  const { controller, previews, calls, tokenReads } = await fixture(t)
  assert.equal(tokenReads(), 0)
  assert.equal(controller.hasActiveWork(), false)
  assert.deepEqual(controller.getInternetWriteStatus(), { configured: true })
  assert.equal(tokenReads(), 1)
  assert.equal(previews.length, 0)
  assert.equal(calls.length, 0)
})

test('Prepare returns exact detached scope and five-minute expiry without a dialog, network lease or credential persistence', async t => {
  const { controller, owner, policy, previews, calls, file, secret, confirm } = await fixture(t)
  owner.projectId = 'project_7'
  const receipt = controller.prepareGithubIssue(request())
  assert.deepEqual(Object.keys(receipt).sort(), ['id', 'scope', 'owner', 'repo', 'title', 'body', 'expiresAt'].sort())
  assert.match(receipt.id, /^internet_approval_[0-9a-f]{32}$/u)
  assert.deepEqual(receipt.scope, { chatId: 'chat_a', projectId: 'project_7' })
  assert.equal(receipt.expiresAt, '2026-10-14T20:05:00.000Z')
  assert.equal(receipt.title, request().title)
  assert.equal(receipt.body, request().body)
  assert.equal(policy.hasActiveWork(), false)
  assert.equal(previews.length, 0)
  assert.equal(calls.length, 0)
  assert.equal(JSON.stringify(receipt).includes(secret.value), false)
  const persisted = await readFile(file, 'utf8')
  assert.equal(persisted.includes(secret.value), false)
  assert.equal(persisted.includes(request().title), false)
  const original = structuredClone(receipt)
  receipt.title = 'renderer changed'; receipt.scope.projectId = 'project_forged'
  assert.equal((await confirm(receipt)).status, 'created')
  assert.deepEqual(previews[0], original)
  assert.equal(calls[0].payload.title, request().title)
})

test('Prepare requires an idle canonical owner, enabled policy and exact API permission before any confirmation', async t => {
  for (const option of [{ enabled: false }, { denyApi: true }]) {
    const setup = await fixture(t, option)
    assert.throws(() => setup.controller.prepareGithubIssue(request()), code(option.enabled === false ? 'disabled' : 'domain_denied'))
    assert.equal(setup.previews.length, 0); assert.equal(setup.calls.length, 0)
  }
  const { controller, owner, calls } = await fixture(t)
  for (const [field, value, expected] of [['busy', true, 'busy'], ['ready', false, 'unavailable'], ['archived', true, 'unavailable'], ['chatId', 'chat_b', 'owner_changed']]) {
    const before = owner[field]; owner[field] = value
    assert.throws(() => controller.prepareGithubIssue(request()), code(expected))
    owner[field] = before; controller.synchronizeOwner()
  }
  assert.equal(calls.length, 0)
})

test('Missing, control-bearing or throwing token readers expose only not-configured status and safe errors', async t => {
  for (const value of [null, '', 'secret with spaces', 'secret\nheader', '秘密', 'x'.repeat(4097)]) {
    const { controller } = await fixture(t, { readToken: () => value })
    assert.deepEqual(controller.getInternetWriteStatus(), { configured: false })
    assert.throws(() => controller.prepareGithubIssue(request()), code('not_configured'))
  }
  const { controller } = await fixture(t, { readToken: () => { throw new Error('credential=do-not-disclose') } })
  assert.deepEqual(controller.getInternetWriteStatus(), { configured: false })
  assert.throws(() => controller.prepareGithubIssue(request()), error => error.code === 'not_configured' && !error.message.includes('do-not-disclose'))
})

test('Repeated prepare revokes the previous receipt; confirmation consumes once and cannot replace reviewed contents', async t => {
  const { controller, confirm, calls, previews } = await fixture(t)
  const first = controller.prepareGithubIssue(request()), second = controller.prepareGithubIssue({ ...request(), title: 'Second exact title' })
  assert.notEqual(first.id, second.id)
  assert.equal((await confirm(first)).error, 'approval_invalid')
  assert.equal((await confirm(second)).error, 'approval_invalid')
  const fresh = controller.prepareGithubIssue(request())
  assert.equal((await controller.confirmGithubIssue({ chatId: 'chat_a', approvalId: fresh.id, title: 'replacement' })).error, 'invalid_request')
  assert.equal((await confirm(fresh)).error, 'approval_invalid')
  const approved = controller.prepareGithubIssue(request())
  assert.equal((await confirm(approved)).status, 'created')
  assert.equal((await confirm(approved)).error, 'approval_invalid')
  assert.equal(calls.length, 1); assert.equal(previews.length, 1)
})

test('Independent native confirmation receives full detached contents and defaults to no write unless exactly true', async t => {
  for (const answer of [false, undefined, null, 1, 'true']) {
    const { controller, confirm, calls, previews } = await fixture(t, { confirm: async () => answer })
    const receipt = controller.prepareGithubIssue(request()), result = await confirm(receipt)
    assert.deepEqual(result, { status: 'cancelled', issueUrl: null, number: null, error: 'cancelled' })
    assert.equal(calls.length, 0)
    assert.deepEqual(previews[0], receipt)
    assert.equal((await confirm(receipt)).error, 'approval_invalid')
  }
  const { controller, confirm, calls } = await fixture(t, { confirm: async preview => { preview.title = 'native callback mutation'; preview.scope.chatId = 'chat_b'; return true } })
  assert.equal((await confirm(controller.prepareGithubIssue(request()))).status, 'created')
  assert.deepEqual(calls[0].payload, { owner: request().owner, repo: request().repo, title: request().title, body: request().body })
})

test('Expiry is checked before the dialog and again before dispatch; a slow successful POST remains a known creation', async t => {
  const before = await fixture(t)
  const expired = before.controller.prepareGithubIssue(request())
  before.clock.value += 300000
  assert.equal((await before.confirm(expired)).error, 'approval_expired')
  assert.equal(before.previews.length, 0)
  const dialog = deferred(), during = await fixture(t, { confirm: () => dialog.promise })
  const pending = during.confirm(during.controller.prepareGithubIssue(request()))
  during.clock.value += 300000; dialog.resolve(true)
  assert.equal((await pending).error, 'approval_expired')
  assert.equal(during.calls.length, 0)
  const write = deferred(), after = await fixture(t, { write: () => write.promise })
  const running = after.confirm(after.controller.prepareGithubIssue(request()))
  await Promise.resolve()
  after.clock.value += 300000; write.resolve(created())
  assert.equal((await running).status, 'created')
})

test('Wrong Chat and A-B-A scope, Project, mode, readiness or archive transitions revoke pending receipts', async t => {
  const wrong = await fixture(t), receipt = wrong.controller.prepareGithubIssue(request())
  assert.equal((await wrong.controller.confirmGithubIssue({ chatId: 'chat_b', approvalId: receipt.id })).error, 'owner_changed')
  assert.equal(wrong.previews.length, 0)
  for (const [field, value] of [['chatId', 'chat_b'], ['projectId', 'project_b'], ['mode', 'work'], ['ready', false], ['archived', true]]) {
    const { controller, owner, confirm, calls, previews } = await fixture(t)
    const approval = controller.prepareGithubIssue(request()), original = owner[field]
    owner[field] = value; controller.synchronizeOwner()
    owner[field] = original; controller.synchronizeOwner()
    assert.equal((await confirm(approval)).error, 'approval_invalid', field)
    assert.equal(calls.length, 0); assert.equal(previews.length, 0)
  }
})

test('Policy Off and even a missed Off-On notification revoke a pending approval through policy revision', async t => {
  const { controller, confirm, update, previews, calls } = await fixture(t)
  const first = controller.prepareGithubIssue(request())
  await update(false); controller.synchronizeOwner()
  await update(true); controller.synchronizeOwner()
  assert.equal((await confirm(first)).error, 'approval_invalid')
  const second = controller.prepareGithubIssue(request())
  await update(false); await update(true)
  assert.notEqual((await confirm(second)).status, 'created')
  assert.equal(previews.length, 0); assert.equal(calls.length, 0)
})

test('A token change or removal after preview cannot reuse the same approval under a different account', async t => {
  for (const next of ['different-test-secret', null]) {
    const { controller, secret, confirm, calls, previews } = await fixture(t)
    const receipt = controller.prepareGithubIssue(request())
    secret.value = next
    assert.equal((await confirm(receipt)).error, next === null ? 'not_configured' : 'approval_invalid')
    assert.equal(previews.length, 0); assert.equal(calls.length, 0)
  }
  const dialog = deferred(), setup = await fixture(t, { confirm: () => dialog.promise })
  const running = setup.confirm(setup.controller.prepareGithubIssue(request()))
  setup.secret.value = 'different-test-secret'; dialog.resolve(true)
  assert.equal((await running).error, 'approval_invalid')
  assert.equal(setup.calls.length, 0)
})

test('Native dialog ownership remains busy until its true completion and concurrent approvals cannot bypass it', async t => {
  const dialog = deferred(), { controller, policy, confirm, calls, previews, events } = await fixture(t, { confirm: () => dialog.promise })
  const receipt = controller.prepareGithubIssue(request()), running = confirm(receipt)
  assert.equal(controller.hasActiveWork(), true); assert.equal(policy.hasActiveWork(), true)
  assert.throws(() => controller.prepareGithubIssue(request()), code('busy'))
  assert.equal((await confirm(receipt)).error, 'busy')
  let drained = false
  const idle = controller.whenIdle().then(() => { drained = true })
  controller.cancel('chat_a')
  await Promise.resolve()
  assert.equal(drained, false); assert.equal(controller.hasActiveWork(), true)
  dialog.resolve(true)
  assert.equal((await running).status, 'cancelled')
  await idle
  assert.equal(calls.length, 0); assert.equal(previews.length, 1)
  assert.equal(policy.hasActiveWork(), false)
  assert.equal(events.at(-1).busy, false)
})

test('Scope, permission, owner busy and shutdown changes during native confirmation prevent dispatch', async t => {
  for (const change of ['chat', 'policy', 'busy', 'shutdown', 'invalidate']) {
    const dialog = deferred(), setup = await fixture(t, { confirm: () => dialog.promise })
    const running = setup.confirm(setup.controller.prepareGithubIssue(request()))
    if (change === 'chat') { setup.owner.chatId = 'chat_b'; setup.controller.synchronizeOwner(); setup.owner.chatId = 'chat_a'; setup.controller.synchronizeOwner() }
    if (change === 'policy') { await setup.update(false); setup.controller.synchronizeOwner() }
    if (change === 'busy') setup.owner.busy = true
    if (change === 'shutdown') setup.controller.shutdown()
    if (change === 'invalidate') setup.controller.invalidate()
    assert.equal(setup.controller.hasActiveWork(), true)
    dialog.resolve(true)
    const result = await running
    assert.equal(result.status, change === 'busy' ? 'failed' : 'cancelled', change)
    assert.equal(setup.calls.length, 0, change)
  }
})

test('Exactly one approved payload and Main-only token reach the callback; public previews, results and events contain no credential', async t => {
  const { controller, secret, confirm, calls, previews, events, policy } = await fixture(t)
  const receipt = controller.prepareGithubIssue(request()), result = await confirm(receipt)
  assert.equal(calls.length, 1)
  assert.deepEqual(Object.keys(calls[0].payload).sort(), ['owner', 'repo', 'title', 'body'].sort())
  assert.equal(calls[0].token, secret.value)
  assert.equal(calls[0].signal instanceof AbortSignal, true)
  assert.equal(calls[0].signal.aborted, false)
  assert.deepEqual(result, { status: 'created', issueUrl: created().url, number: 7, error: null })
  assert.equal(JSON.stringify({ receipt, result, previews, events, status: controller.getInternetWriteStatus() }).includes(secret.value), false)
  assert.equal(policy.hasActiveWork(), false)
})

test('Cancellation after write invocation keeps physical capacity and unknown outcomes never become cancelled or retry', async t => {
  const held = deferred(), { controller, policy, confirm, calls } = await fixture(t, { write: () => held.promise })
  const running = confirm(controller.prepareGithubIssue(request()))
  await Promise.resolve()
  assert.equal(calls.length, 1)
  controller.cancel('chat_a')
  assert.equal(calls[0].signal.aborted, true)
  assert.equal(controller.hasActiveWork(), true); assert.equal(policy.hasActiveWork(), true)
  let drained = false
  const idle = controller.whenIdle().then(() => { drained = true })
  await Promise.resolve(); assert.equal(drained, false)
  assert.throws(() => controller.prepareGithubIssue(request()), code('busy'))
  held.resolve(outcome('unknown'))
  assert.deepEqual(await running, { status: 'outcome_unknown', issueUrl: null, number: null, error: 'outcome_unknown' })
  await idle
  assert.equal(calls.length, 1); assert.equal(policy.hasActiveWork(), false)
})

test('Verified creation remains truthful after scope change, Off, token change or shutdown without publishing result events', async t => {
  for (const change of ['scope', 'off', 'token', 'shutdown']) {
    const held = deferred(), setup = await fixture(t, { write: () => held.promise })
    const running = setup.confirm(setup.controller.prepareGithubIssue(request()))
    await Promise.resolve(); assert.equal(setup.calls.length, 1)
    if (change === 'scope') { setup.owner.chatId = 'chat_b'; setup.controller.synchronizeOwner() }
    if (change === 'off') { await setup.update(false); setup.controller.synchronizeOwner() }
    if (change === 'token') setup.secret.value = null
    if (change === 'shutdown') setup.controller.shutdown()
    held.resolve(created())
    assert.equal((await running).status, 'created', change)
    assert.equal(setup.events.every(event => Object.keys(event).length === 1 && typeof event.busy === 'boolean'), true)
  }
})

test('Only proven not-sent can become cancellation; provider rejection and unverified transport errors stay truthful', async t => {
  for (const [transport, expectedStatus, expectedError] of [['not_sent', 'failed', 'network_failed'], ['rejected', 'failed', 'rejected'], ['unknown', 'outcome_unknown', 'outcome_unknown']]) {
    const { controller, confirm, calls } = await fixture(t, { write: async () => outcome(transport) })
    const result = await confirm(controller.prepareGithubIssue(request()))
    assert.equal(result.status, expectedStatus); assert.equal(result.error, expectedError); assert.equal(calls.length, 1)
  }
  const held = deferred(), cancelled = await fixture(t, { write: () => held.promise })
  const pending = cancelled.confirm(cancelled.controller.prepareGithubIssue(request()))
  await Promise.resolve(); cancelled.controller.cancel('chat_a'); held.resolve(outcome('not_sent'))
  assert.equal((await pending).status, 'cancelled')
  const exception = await fixture(t, { write: async () => { throw new Error('Authorization: secret-do-not-disclose') } })
  const result = await exception.confirm(exception.controller.prepareGithubIssue(request()))
  assert.deepEqual(result, { status: 'outcome_unknown', issueUrl: null, number: null, error: 'outcome_unknown' })
  assert.equal(exception.calls.length, 1)
})

test('Malformed or substituted transport success is unknown and cannot publish a forged Issue identity', async t => {
  for (const response of [null, { ...created(), token: 'leak' }, { ...created(), number: 8 },
    { ...created(), url: 'https://github.com/other/repo/issues/7' }, { ...created(), url: 'https://evil.com/issues/7' },
    { ...created(), url: 'https://github.com/octocat/Hello-World/issues/7#token' }, { ...outcome('not_sent'), number: 7 },
    { ...outcome('rejected'), url: created().url }, { outcome: 'invented', url: null, number: null }]) {
    const { controller, confirm, calls } = await fixture(t, { write: async () => response })
    assert.equal((await confirm(controller.prepareGithubIssue(request()))).status, 'outcome_unknown')
    assert.equal(calls.length, 1)
  }
  const { controller, confirm } = await fixture(t, { write: async () => ({ ...created(), url: 'https://github.com/Octocat/hello-world/issues/7' }) })
  assert.equal((await confirm(controller.prepareGithubIssue(request()))).status, 'created')
})

test('Native dialog exceptions and observer failures cannot leak secrets, grant approval or prevent physical release', async t => {
  const dialog = await fixture(t, { confirm: async () => { throw new Error('secret-token-in-native-diagnostic') } })
  const result = await dialog.confirm(dialog.controller.prepareGithubIssue(request()))
  assert.deepEqual(result, { status: 'failed', issueUrl: null, number: null, error: 'unavailable' })
  assert.equal(dialog.calls.length, 0); assert.equal(dialog.controller.hasActiveWork(), false)
  const observer = await fixture(t, { onState: () => { throw new Error('listener diagnostic') } })
  assert.equal((await observer.confirm(observer.controller.prepareGithubIssue(request()))).status, 'created')
  assert.equal(observer.controller.hasActiveWork(), false)
})

test('Receipt parser preserves complete content and rejects altered scope shapes, malformed expiry and credential additions', async t => {
  const { controller } = await fixture(t)
  const receipt = controller.prepareGithubIssue(request())
  assert.deepEqual(parseGithubIssueApproval(receipt), receipt)
  for (const changes of [{ token: 'secret' }, { scope: { chatId: 'chat_a', projectId: null, mode: 'work' } },
    { scope: { chatId: 'chat_a', projectId: '../other' } }, { expiresAt: '2026-10-14' }, { expiresAt: '2026-02-30T00:00:00.000Z' }, { body: 'x'.repeat(4001) }]) {
    assert.throws(() => parseGithubIssueApproval({ ...receipt, ...changes }), TypeError)
  }
})
