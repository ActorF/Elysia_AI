/** Exercise default-off Internet persistence, strict settings boundaries and revoked physical request ownership. */
import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile, rename, symlink, link, mkdir } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { InternetPolicyService } from '../dist-electron/internet-policy.js'
import { BUILTIN_INTERNET_DOMAINS, defaultInternetPolicy, effectiveInternetDomains, parseInternetPolicyState, parseUpdateInternetPolicyRequest } from '../dist-electron/internet-policy-protocol.js'

const request = (revision = 0, changes = {}) => ({ expectedRevision: revision, enabled: true,
  allowedDomains: ['api.github.com'], proxy: { mode: 'direct' }, timeoutMs: 15000, ...changes })
const code = expected => error => error.code === expected
async function fixture(t, options = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-internet-policy-'))
  t.after(async () => {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    assert.match(path.basename(directory), /^elysia-internet-policy-/u)
    await rm(directory, { recursive: true, force: true })
  })
  const file = path.join(directory, 'policy.json')
  return { file, service: new InternetPolicyService(file, options) }
}

test('Missing policy lists fixed providers but stays disabled without creating a file or admitting requests', async t => {
  const { file, service } = await fixture(t)
  assert.deepEqual(service.getState(), defaultInternetPolicy())
  assert.deepEqual(await service.load(), defaultInternetPolicy())
  await assert.rejects(readFile(file), { code: 'ENOENT' })
  assert.throws(() => service.acquire(), code('disabled'))
  assert.equal(service.hasActiveWork(), false)
})

test('Strict portable schema rejects unknown fields, credentials, wildcard domains, local hosts and unbounded values', () => {
  for (const changes of [{ token: 'secret' }, { allowedDomains: ['*.github.com'] }, { allowedDomains: ['API.github.com'] },
    { allowedDomains: ['localhost'] }, { allowedDomains: ['127.0.0.1'] }, { allowedDomains: ['host.local'] },
    { allowedDomains: ['https://github.com'] }, { allowedDomains: ['github.com', 'github.com'] },
    { proxy: { mode: 'local-http', port: 0 } }, { proxy: { mode: 'system' } }, { proxy: { mode: 'direct', secret: 'x' } },
    { timeoutMs: 999 }, { timeoutMs: 60001 }, { expectedRevision: -1 }]) {
    assert.throws(() => parseUpdateInternetPolicyRequest(request(0, changes)), TypeError)
  }
  assert.deepEqual(parseUpdateInternetPolicyRequest(request(0, { proxy: { mode: 'local-http', port: 7890 } })).proxy,
    { mode: 'local-http', port: 7890 })
  assert.throws(() => parseInternetPolicyState({ ...defaultInternetPolicy(), warning: 'private-path' }), TypeError)
})

test('Reviewed settings persist but every restart begins Off, and detached state cannot add permissions', async t => {
  const { file, service } = await fixture(t)
  const state = await service.update(request())
  assert.equal(state.revision, 1)
  assert.equal(state.enabled, true)
  state.allowedDomains.push('evil.com')
  assert.deepEqual(service.getState().allowedDomains, [...BUILTIN_INTERNET_DOMAINS])
  const loaded = await new InternetPolicyService(file).load()
  assert.equal(loaded.enabled, false)
  assert.deepEqual(loaded.allowedDomains, [...BUILTIN_INTERNET_DOMAINS])
  const document = JSON.parse(await readFile(file, 'utf8'))
  assert.equal(document.schemaVersion, 1)
  assert.equal(document.policy.enabled, false)
  document.policy.enabled = true
  await writeFile(file, JSON.stringify(document))
  assert.equal((await new InternetPolicyService(file).load()).enabled, false)
})

test('Legacy empty policies gain only fixed providers and require a new session enable', async t => {
  const { file, service } = await fixture(t)
  await writeFile(file, JSON.stringify({ schemaVersion: 1, policy: { ...defaultInternetPolicy(), enabled: true, allowedDomains: [] } }))
  const initial = await service.load()
  assert.equal(initial.enabled, false)
  assert.deepEqual(initial.allowedDomains, [...BUILTIN_INTERNET_DOMAINS])
  assert.throws(() => service.acquire(), code('disabled'))
  const enabled = await service.update(request(0, { allowedDomains: ['docs.python.org'] }))
  assert.deepEqual(enabled.allowedDomains, effectiveInternetDomains(['docs.python.org']))
  assert.equal(enabled.allowedDomains.includes('result-site.org'), false)
  assert.equal(enabled.allowedDomains.includes('private.github.com'), false)
  const lease = service.acquire()
  assert.deepEqual(lease.policy.allowedDomains, enabled.allowedDomains)
  lease.release()
})

test('All 32 custom domains survive an effective-state Off round trip without loosening the custom bound', async t => {
  const { service } = await fixture(t)
  const domains = Array.from({ length: 32 }, (_, index) => `custom${index}.org`)
  const state = await service.update(request(0, { allowedDomains: domains }))
  assert.equal(state.allowedDomains.length, 32 + BUILTIN_INTERNET_DOMAINS.length)
  assert.equal((await service.update(request(1, { enabled: false, allowedDomains: state.allowedDomains }))).enabled, false)
  assert.throws(() => parseUpdateInternetPolicyRequest(request(2, { allowedDomains: [...domains, 'overflow.org'] })), TypeError)
})

test('Corrupt, oversized and surplus-field documents load off with a safe warning and can be explicitly replaced', async t => {
  const { file, service } = await fixture(t)
  for (const content of ['{', ' '.repeat(16385), JSON.stringify({ schemaVersion: 1, policy: defaultInternetPolicy(), secret: 'x' })]) {
    await writeFile(file, content)
    const state = await service.load()
    assert.equal(state.enabled, false)
    assert.equal(state.warning, 'storage_unavailable')
    assert.throws(() => service.acquire(), code('disabled'))
  }
  assert.equal((await service.update(request())).enabled, true)
})

test('CAS serializes competing service instances without losing a later revision', async t => {
  const { file, service } = await fixture(t)
  const second = new InternetPolicyService(file)
  const results = await Promise.allSettled([service.update(request()), second.update(request(0, { allowedDomains: ['en.wikipedia.org'] }))])
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1)
  assert.equal(results.find(result => result.status === 'rejected').reason.code, 'conflict')
  const persisted = await new InternetPolicyService(file).load()
  assert.equal(persisted.revision, 1)
})

test('Changing policy cancels immediately but cancelled leases and idle waits retain physical ownership', async t => {
  const { service } = await fixture(t)
  await service.update(request())
  const lease = service.acquire()
  const changing = service.update(request(1, { enabled: false }))
  assert.equal(lease.signal.aborted, true)
  assert.equal(service.getState().enabled, false)
  assert.throws(() => service.acquire(), code('disabled'))
  let idle = false
  const draining = service.whenIdle().then(() => { idle = true })
  await changing
  assert.equal(service.hasActiveWork(), true)
  await Promise.resolve()
  assert.equal(idle, false)
  assert.throws(() => lease.assertCurrent(), code('policy_changed'))
  lease.release(); lease.release()
  await draining
  assert.equal(service.hasActiveWork(), false)
})

test('Write failure keeps admission off and never restores a previously enabled policy in memory', async t => {
  let fail = false
  const { service } = await fixture(t, { replaceFile: async (source, target) => {
    if (fail) throw new Error('secret-native-path')
    await rename(source, target)
  } })
  await service.update(request())
  const lease = service.acquire()
  fail = true
  await assert.rejects(service.update(request(1)), error => {
    assert.doesNotMatch(error.message, /secret-native/u)
    return error.code === 'storage_unavailable'
  })
  assert.equal(lease.signal.aborted, true)
  assert.equal(service.getState().enabled, false)
  assert.equal(service.getState().warning, 'storage_unavailable')
  lease.release()
})

test('Caller cancellation preserves capacity, cancelAll preserves saved intent, and shutdown never reopens admission', async t => {
  const { service } = await fixture(t)
  await service.update(request())
  const abort = new AbortController(), lease = service.acquire(abort.signal)
  abort.abort()
  assert.equal(lease.signal.aborted, true)
  assert.equal(service.hasActiveWork(), true)
  lease.release()
  service.cancelAll()
  assert.equal(service.getState().enabled, true)
  const next = service.acquire()
  service.shutdown()
  assert.equal(next.signal.aborted, true)
  assert.throws(() => service.acquire(), code('shutdown'))
  await assert.rejects(service.update(request(1)), code('shutdown'))
  next.release()
  await service.whenIdle()
})

test('A held atomic save cannot re-enable admission after shutdown and idle includes persistence', async t => {
  let resume, entered
  const began = new Promise(resolve => { entered = resolve })
  const held = new Promise(resolve => { resume = resolve })
  const { service } = await fixture(t, { replaceFile: async (source, target) => { entered(); await held; await rename(source, target) } })
  const saving = service.update(request())
  await began
  service.shutdown()
  let idle = false
  const drain = service.whenIdle().then(() => { idle = true })
  await Promise.resolve()
  assert.equal(idle, false)
  resume()
  await saving; await drain
  assert.equal(service.getState().enabled, false)
})

test('Overlapping stale Off refreshes the persisted revision while staying Off so a reviewed retry can save', async t => {
  let resume, entered
  const began = new Promise(resolve => { entered = resolve })
  const held = new Promise(resolve => { resume = resolve })
  let writes = 0
  const { file, service } = await fixture(t, { replaceFile: async (source, target) => {
    if (++writes === 1) { entered(); await held }
    await rename(source, target)
  } })
  const enabling = service.update(request())
  await began
  const disabling = service.update(request(0, { enabled: false }))
  const conflict = assert.rejects(disabling, code('conflict'))
  resume(); await enabling; await conflict
  assert.equal(service.getState().revision, 1)
  assert.equal(service.getState().enabled, false)
  assert.equal((await service.update(request(1, { enabled: false }))).revision, 2)
  assert.equal((await new InternetPolicyService(file).load()).enabled, false)
})

test('Linked preference targets and directory ancestors cannot redirect a save or alter their destination', async t => {
  const { file, service } = await fixture(t)
  const directory = path.dirname(file), other = path.join(directory, 'other.json')
  await writeFile(other, '{"private":"untouched"}')
  await link(other, file)
  assert.equal((await service.load()).warning, 'storage_unavailable')
  await assert.rejects(service.update(request()), code('storage_unavailable'))
  assert.equal(await readFile(other, 'utf8'), '{"private":"untouched"}')
  await rm(file)
  const target = path.join(directory, 'real'), alias = path.join(directory, 'alias')
  await mkdir(target)
  await symlink(target, alias, process.platform === 'win32' ? 'junction' : 'dir')
  const linked = new InternetPolicyService(path.join(alias, 'policy.json'))
  assert.equal((await linked.load()).warning, 'storage_unavailable')
  await assert.rejects(linked.update(request()), code('storage_unavailable'))
  await assert.rejects(readFile(path.join(target, 'policy.json')), { code: 'ENOENT' })
})
