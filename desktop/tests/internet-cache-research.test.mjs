/** Verify explicit cache integration preserves canonical owner admission, offline provenance and cancellation boundaries across every read provider. */
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { InternetPolicyService } from '../dist-electron/internet-policy.js'
import { InternetResearchController } from '../dist-electron/internet-research.js'
import { parseInternetResearchState } from '../dist-electron/internet-protocol.js'

const at = Date.parse('2026-10-14T22:00:00.000Z')
const code = expected => error => error.code === expected
const query = (text = 'Evidence', language = 'en') => ({ chatId: 'chat_a', query: text, language })
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function fixture(t, options = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-internet-cache-research-'))
  const policy = new InternetPolicyService(path.join(directory, 'policy.json'))
  await policy.update({ expectedRevision: 0, enabled: true, allowedDomains: ['en.wikipedia.org', 'zh.wikipedia.org', 'example.com', 'api.open-meteo.com', 'api.github.com'],
    proxy: { mode: 'direct' }, timeoutMs: 15000 })
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', archived: false, ready: true, busy: false }
  const clock = { value: at }, calls = [], events = []
  let identity = 0, transportCalls = 0
  const source = (provider = 'web', url = 'https://example.com/', extra = {}) => ({
    id: `internet_${(++identity).toString(16).padStart(32, '0')}`, provider, url, title: 'Original source',
    snippet: 'Original excerpt', content: provider === 'wikipedia' ? null : 'Original body', publishedAt: null,
    modifiedAt: null, fetchedAt: new Date(clock.value).toISOString(), fromCache: false, truncated: false, ...extra })
  const controller = new InternetResearchController({ policy, getOwner: () => owner, now: () => new Date(clock.value),
    transport: { read: async () => { transportCalls += 1; throw new Error('Unexpected transport or DNS path') } },
    onState: value => events.push(value),
    search: async (request, signal) => {
      calls.push({ kind: 'search', request, signal })
      return options.search === undefined ? { sources: [source('wikipedia', `https://${request.language}.wikipedia.org/?curid=1`)], fetchedAt: new Date(clock.value).toISOString() }
        : options.search(request, signal, source)
    },
    readPage: async (url, signal) => { calls.push({ kind: 'page', url, signal }); return options.readPage === undefined ? source('web', url) : options.readPage(url, signal, source) },
    weather: async (location, signal) => {
      calls.push({ kind: 'weather', location, signal })
      return source('weather', `https://api.open-meteo.com/v1/forecast?latitude=${location.latitude.toFixed(4)}&longitude=${location.longitude.toFixed(4)}`)
    },
    github: async (repository, signal) => { calls.push({ kind: 'github', repository, signal }); return source('github', `https://api.github.com/repos/${repository.owner}/${repository.repo}`) },
  })
  t.after(async () => {
    controller.shutdown(); policy.shutdown()
    await Promise.all([controller.whenIdle(), policy.whenIdle()])
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    assert.match(path.basename(directory), /^elysia-internet-cache-research-/u)
    await rm(directory, { recursive: true, force: true })
  })
  const restore = (id, allowExpired = false) => controller.restoreCache({ chatId: 'chat_a', cacheId: id, allowExpired })
  const update = async (enabled, allowedDomains = []) => {
    const state = policy.getState()
    await policy.update({ expectedRevision: state.revision, enabled, allowedDomains, proxy: state.proxy, timeoutMs: state.timeoutMs })
    controller.synchronizeOwner()
  }
  return { controller, owner, clock, calls, events, policy, source, restore, update, transportCalls: () => transportCalls }
}

test('Every successful provider creates a scoped entry with appropriate TTL and original service provenance', async t => {
  const f = await fixture(t)
  await f.controller.search(query())
  await f.controller.readPage({ chatId: 'chat_a', url: 'https://example.com/article' })
  await f.controller.weather({ chatId: 'chat_a', latitude: 41.8781, longitude: -87.6298 })
  await f.controller.github({ chatId: 'chat_a', owner: 'octocat', repo: 'Hello-World' })
  const state = f.controller.getCache('chat_a')
  assert.equal(state.kind, 'internet.cache')
  assert.deepEqual(state.owner, { chatId: 'chat_a', projectId: null })
  assert.equal(state.entries.length, 4)
  const history = f.controller.getState('chat_a').history
  for (const entry of state.entries) {
    const isPage = entry.label === 'https://example.com/article'
    assert.equal(Date.parse(entry.expiresAt) - Date.parse(entry.fetchedAt), isPage ? 1800000 : 300000)
    const restored = f.restore(entry.id)
    assert.equal(restored.sources[0].fromCache, true)
    assert.equal(restored.sources[0].fetchedAt, new Date(at).toISOString())
    assert.deepEqual(restored.history, history)
  }
  assert.deepEqual(new Set(f.controller.getState('chat_a').sources.map(source => source.provider)), new Set(['wikipedia', 'web', 'weather', 'github']))
  assert.equal(f.calls.length, 4)
  assert.equal(f.transportCalls(), 0)
})

test('Restoration remains explicitly local after Off and after allowlist removal, without acquiring policy or making provider calls', async t => {
  const f = await fixture(t)
  await f.controller.search(query())
  const entry = f.controller.getCache('chat_a').entries[0]
  const original = f.controller.getState('chat_a').sources[0]
  await f.update(false)
  assert.equal(f.controller.getCache('chat_a').entries[0].id, entry.id)
  const restored = f.restore(entry.id).sources[0]
  assert.notEqual(restored.id, original.id)
  assert.deepEqual({ ...restored, id: original.id, fromCache: false }, original)
  assert.equal(f.policy.hasActiveWork(), false)
  await f.update(true, ['api.github.com'])
  assert.equal(f.restore(entry.id).sources[0].fromCache, true)
  assert.equal(f.calls.length, 1); assert.equal(f.transportCalls(), 0)
})

test('Expired restoration requires explicit permission and uses safe cache errors without altering visible evidence or history', async t => {
  const f = await fixture(t)
  await f.controller.weather({ chatId: 'chat_a', latitude: 0, longitude: 0 })
  const entry = f.controller.getCache('chat_a').entries[0], before = f.controller.getState('chat_a')
  f.clock.value += 300000
  assert.equal(f.controller.getCache('chat_a').entries[0].expired, true)
  assert.throws(() => f.restore(entry.id), code('cache_expired'))
  assert.deepEqual(f.controller.getState('chat_a'), before)
  assert.throws(() => f.restore(`internet_cache_${'0'.repeat(32)}`), code('cache_not_found'))
  assert.throws(() => f.controller.restoreCache({ chatId: 'chat_a', cacheId: entry.id }), code('invalid_request'))
  assert.equal(f.restore(entry.id, true).sources[0].fetchedAt, entry.fetchedAt)
  for (const error of ['cache_expired', 'cache_not_found']) assert.equal(parseInternetResearchState({ ...before, error, status: 'error' }).error, error)
})

test('A successful empty search is cached and restoring it neither invents a new query nor removes existing evidence', async t => {
  const f = await fixture(t, { search: async () => ({ sources: [], fetchedAt: new Date(at).toISOString() }) })
  await f.controller.readPage({ chatId: 'chat_a', url: 'https://example.com/' })
  await f.controller.search(query('No matches'))
  const entry = f.controller.getCache('chat_a').entries[0], before = f.controller.getState('chat_a')
  assert.equal(entry.sourceCount, 0)
  assert.deepEqual(f.restore(entry.id).sources, before.sources)
  assert.deepEqual(f.controller.getState('chat_a').history, before.history)
  assert.equal(f.calls.length, 2)
})

test('Closed input keys distinguish searches and URLs, normalize coordinates and repository case, and accommodate maximum URL length', async t => {
  const f = await fixture(t)
  await f.controller.search(query('Evidence', 'en'))
  await f.controller.search(query('Evidence', 'zh'))
  await f.controller.search(query('Different', 'en'))
  assert.equal(f.controller.getCache('chat_a').entries.length, 3)
  const before = f.controller.getCache('chat_a').entries.find(entry => entry.label === 'Wikipedia (en): Evidence').id
  await f.controller.search(query('Evidence', 'en'))
  assert.equal(f.controller.getCache('chat_a').entries.length, 3)
  assert.notEqual(f.controller.getCache('chat_a').entries[0].id, before)
  await f.controller.weather({ chatId: 'chat_a', latitude: 1.000001, longitude: -0 })
  await f.controller.weather({ chatId: 'chat_a', latitude: 1, longitude: 0 })
  assert.equal(f.controller.getCache('chat_a').entries.length, 4)
  await f.controller.github({ chatId: 'chat_a', owner: 'octocat', repo: 'Hello-World' })
  await f.controller.github({ chatId: 'chat_a', owner: 'OCTOCAT', repo: 'hello-world' })
  assert.equal(f.controller.getCache('chat_a').entries.length, 5)
  const base = 'https://example.com/?q=', url = base + 'x'.repeat(4096 - base.length)
  assert.equal((await f.controller.readPage({ chatId: 'chat_a', url })).error, null)
  assert.equal(f.controller.getCache('chat_a').entries[0].label, url)
})

test('A failed or malformed refresh never replaces a previously successful cached result', async t => {
  let attempt = 0
  const f = await fixture(t, { readPage: async (url, _signal, source) => {
    attempt += 1
    if (attempt === 2) throw new Error('Provider failed')
    return source('web', url, attempt === 3 ? { fromCache: true } : {})
  } })
  const request = { chatId: 'chat_a', url: 'https://example.com/' }
  await f.controller.readPage(request)
  const entry = f.controller.getCache('chat_a').entries[0]
  assert.equal((await f.controller.readPage(request)).error, 'network_failed')
  assert.equal((await f.controller.readPage(request)).error, 'invalid_output')
  assert.deepEqual(f.controller.getCache('chat_a').entries, [entry])
  assert.equal(f.restore(entry.id).error, null)
})

test('A cancelled late refresh cannot poison cache and keeps physical capacity until its callback drains', async t => {
  const held = deferred()
  let count = 0, late
  const f = await fixture(t, { readPage: (url, _signal, source) => {
    late = source('web', url, { title: 'Late result' })
    return ++count === 1 ? Promise.resolve(source('web', url)) : held.promise
  } })
  const request = { chatId: 'chat_a', url: 'https://example.com/' }
  await f.controller.readPage(request)
  const original = f.controller.getCache('chat_a').entries[0]
  const running = f.controller.readPage(request)
  f.controller.cancel('chat_a')
  assert.equal(f.controller.hasActiveWork(), true)
  assert.deepEqual(f.controller.getCache('chat_a').entries, [original])
  assert.throws(() => f.restore(original.id), code('busy'))
  held.resolve(late)
  await running
  assert.deepEqual(f.controller.getCache('chat_a').entries, [original])
  assert.equal(f.restore(original.id).sources[0].title, 'Original source')
})

test('Off revokes an active refresh but retains only earlier completed cache evidence', async t => {
  const held = deferred()
  let count = 0, late
  const f = await fixture(t, { readPage: (url, _signal, source) => {
    late = source('web', url)
    return ++count === 1 ? Promise.resolve(late) : held.promise
  } })
  await f.controller.readPage({ chatId: 'chat_a', url: 'https://example.com/old' })
  const before = f.controller.getCache('chat_a').entries
  const running = f.controller.readPage({ chatId: 'chat_a', url: 'https://example.com/new' })
  await f.update(false)
  held.resolve(late); await running
  assert.deepEqual(f.controller.getCache('chat_a').entries, before)
  assert.equal(f.restore(before[0].id).sources[0].url, 'https://example.com/old')
})

test('Canonical owner and idle admission guard restore while metadata remains readable during another operation', async t => {
  const f = await fixture(t)
  await f.controller.search(query())
  const entry = f.controller.getCache('chat_a').entries[0]
  f.owner.busy = true
  assert.equal(f.controller.getCache('chat_a').entries.length, 1)
  assert.throws(() => f.restore(entry.id), code('busy'))
  assert.throws(() => f.controller.restoreCache({ chatId: 'chat_b', cacheId: entry.id, allowExpired: true }), code('owner_changed'))
  f.owner.busy = false
  assert.equal(f.restore(entry.id).sources[0].fromCache, true)
})

test('Chat A-B-A, Project, readiness and archive changes scrub cached evidence and retired identities', async t => {
  for (const [field, changed] of [['chatId', 'chat_b'], ['projectId', 'project_other'], ['ready', false], ['archived', true]]) {
    const f = await fixture(t)
    await f.controller.search(query())
    const entry = f.controller.getCache('chat_a').entries[0], original = f.owner[field]
    f.owner[field] = changed; f.controller.synchronizeOwner()
    f.owner[field] = original; f.controller.synchronizeOwner()
    assert.deepEqual(f.controller.getCache('chat_a').entries, [], field)
    assert.throws(() => f.restore(entry.id), code('cache_not_found'))
  }
})

test('Clear and renderer invalidation scrub cache; late results after a scope round trip cannot repopulate it', async t => {
  const held = deferred()
  let late
  const f = await fixture(t, { readPage: (url, _signal, source) => { late = source('web', url); return held.promise } })
  await f.controller.search(query())
  f.controller.clear('chat_a')
  assert.deepEqual(f.controller.getCache('chat_a').entries, [])
  await f.controller.search(query())
  f.controller.invalidate()
  assert.deepEqual(f.controller.getCache('chat_a').entries, [])
  const running = f.controller.readPage({ chatId: 'chat_a', url: 'https://example.com/' })
  f.owner.chatId = 'chat_b'; f.controller.synchronizeOwner()
  f.owner.chatId = 'chat_a'; f.controller.synchronizeOwner()
  held.resolve(late); await running
  assert.deepEqual(f.controller.getCache('chat_a').entries, [])
  f.controller.shutdown()
  assert.throws(() => f.controller.getCache('chat_a'), code('unavailable'))
})

test('Cache restoration merges at most ten native sources and repeated reads remain explicit provider requests', async t => {
  const f = await fixture(t)
  await f.controller.readPage({ chatId: 'chat_a', url: 'https://example.com/first' })
  const entry = f.controller.getCache('chat_a').entries[0]
  for (let index = 0; index < 11; index += 1) await f.controller.readPage({ chatId: 'chat_a', url: `https://example.com/${index}` })
  assert.equal(f.controller.getState('chat_a').sources.length, 10)
  const restored = f.restore(entry.id)
  assert.equal(restored.sources.length, 10)
  assert.equal(restored.sources[0].url, 'https://example.com/first')
  await f.controller.readPage({ chatId: 'chat_a', url: 'https://example.com/first' })
  assert.equal(f.calls.length, 13)
  assert.equal(f.controller.getState('chat_a').sources[0].fromCache, false)
})
