/** Verify bounded offline evidence caching, explicit stale restores, original timestamps, clock rollback and actual UTF-8 JSON eviction. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { InternetCacheStore, INTERNET_CACHE_SHORT_TTL_MS, INTERNET_CACHE_WEB_TTL_MS } from '../dist-electron/internet-cache.js'
import { parseInternetCacheEntry, parseInternetCacheId, parseInternetCacheState,
  parseRestoreInternetCacheRequest } from '../dist-electron/internet-cache-protocol.js'

const at = Date.parse('2026-10-14T22:00:00.000Z')
const iso = value => new Date(value).toISOString()
const source = (index = 1, changes = {}) => ({ id: `internet_${index.toString(16).padStart(32, '0')}`,
  title: `Source ${index}`, url: `https://example.com/page/${index}`, snippet: 'Excerpt', content: 'Original body',
  publishedAt: '2026-10-01T00:00:00.000Z', modifiedAt: '2026-10-13T00:00:00.000Z', fetchedAt: iso(at),
  provider: 'web', fromCache: false, truncated: false, ...changes })
const code = expected => error => error.code === expected
function fixture() { const clock = { value: at }; return { clock, cache: new InternetCacheStore(() => clock.value) } }
function put(cache, key = 'native-key', sources = [source()], ttl = INTERNET_CACHE_WEB_TTL_MS) { return cache.put(key, `Label ${key}`, sources, ttl) }

test('Closed cache protocol admits only opaque native identities and an explicit boolean stale-data decision', () => {
  const id = `internet_cache_${'a'.repeat(32)}`, request = { chatId: 'chat_a', cacheId: id, allowExpired: false }
  assert.equal(parseInternetCacheId(id), id)
  assert.deepEqual(parseRestoreInternetCacheRequest(request), request)
  for (const changes of [{ key: 'private-query' }, { sources: [] }, { token: 'secret' }, { projectId: 'other' },
    { allowExpired: undefined }, { allowExpired: 1 }, { cacheId: 'https://example.com' }, { cacheId: '../file' }, { chatId: 'other' }]) {
    assert.throws(() => parseRestoreInternetCacheRequest({ ...request, ...changes }), TypeError)
  }
})

test('Metadata and state parsing enforce exact fields, bounded counts, unique ids and canonical scope and times', () => {
  const { cache } = fixture(), entry = put(cache)
  const state = { kind: 'internet.cache', owner: { chatId: 'chat_a', projectId: null }, entries: [entry] }
  assert.deepEqual(parseInternetCacheEntry(entry), entry)
  assert.deepEqual(parseInternetCacheState(state), state)
  for (const changes of [{ key: 'private-key' }, { sourceCount: -1 }, { sourceCount: 11 }, { sourceCount: 1.1 },
    { label: '' }, { label: '🌍'.repeat(1025) }, { label: 'bad\u0000' }, { label: '\ud800' }, { expired: 'true' },
    { fetchedAt: '2026-10-14' }, { expiresAt: '2026-02-30T00:00:00.000Z' }]) {
    assert.throws(() => parseInternetCacheEntry({ ...entry, ...changes }), TypeError)
  }
  for (const changes of [{ token: 'secret' }, { entries: [entry, entry] }, { entries: Array(21).fill(entry) },
    { owner: { chatId: 'chat_a', projectId: '../private' } }, { owner: { chatId: 'chat_a', projectId: null, mode: 'chat' } }, { kind: 'other' }]) {
    assert.throws(() => parseInternetCacheState({ ...state, ...changes }), TypeError)
  }
})

test('Empty successful searches remain valid cached results and short and page TTLs match their fixed windows', () => {
  const { cache, clock } = fixture()
  const empty = put(cache, 'search:empty', [], INTERNET_CACHE_SHORT_TTL_MS)
  assert.equal(empty.sourceCount, 0)
  assert.equal(empty.fetchedAt, iso(at))
  assert.equal(empty.expiresAt, iso(at + 300000))
  assert.deepEqual(cache.restore(empty.id, false), [])
  const page = put(cache, 'page')
  assert.equal(page.expiresAt, iso(at + 1800000))
  clock.value = at + 300000
  assert.equal(cache.list().find(item => item.id === empty.id).expired, true)
  assert.equal(cache.list().find(item => item.id === page.id).expired, false)
})

test('Expiry includes the exact TTL boundary, retains stale entries and requires an explicit opt-in without refreshing them', () => {
  const { cache, clock } = fixture(), entry = put(cache, 'search', [source(1, { provider: 'wikipedia', content: null })], INTERNET_CACHE_SHORT_TTL_MS)
  clock.value = at + 299999
  assert.equal(cache.list()[0].expired, false)
  assert.equal(cache.restore(entry.id, false)[0].fromCache, true)
  clock.value = at + 300000
  assert.equal(cache.list()[0].expired, true)
  assert.throws(() => cache.restore(entry.id, false), code('expired'))
  const restored = cache.restore(entry.id, true)
  assert.equal(restored[0].fetchedAt, iso(at))
  assert.equal(cache.list()[0].expiresAt, iso(at + 300000))
  assert.equal(cache.list()[0].expired, true)
  assert.equal(cache.list().length, 1)
})

test('An observed expiration cannot become fresh after the clock rolls backwards', () => {
  const { cache, clock } = fixture(), entry = put(cache, 'short', [source()], INTERNET_CACHE_SHORT_TTL_MS)
  clock.value = at + 300000
  assert.equal(cache.list()[0].expired, true)
  clock.value = at - 3600000
  assert.equal(cache.list()[0].expired, true)
  assert.throws(() => cache.restore(entry.id, false), code('expired'))
  assert.equal(cache.restore(entry.id, true)[0].fetchedAt, iso(at))
  const empty = put(cache, 'empty-after-rollback', [], INTERNET_CACHE_SHORT_TTL_MS)
  assert.equal(empty.fetchedAt, iso(at + 300000))
})

test('Caching old, mixed or future-dated evidence cannot extend its freshness beyond original age or the insertion clock', () => {
  const { cache, clock } = fixture()
  const mixed = put(cache, 'mixed', [source(1), source(2, { fetchedAt: iso(at - 60000) })], INTERNET_CACHE_SHORT_TTL_MS)
  assert.equal(mixed.fetchedAt, iso(at - 60000))
  assert.equal(mixed.expiresAt, iso(at + 240000))
  const future = put(cache, 'future', [source(3, { fetchedAt: iso(at + 3600000) })], INTERNET_CACHE_SHORT_TTL_MS)
  assert.equal(future.fetchedAt, iso(at + 3600000))
  assert.equal(future.expiresAt, iso(at + 300000))
  clock.value = at + 600000
  const stale = cache.restore(mixed.id, true), recached = put(cache, 'recached', stale, INTERNET_CACHE_SHORT_TTL_MS)
  assert.equal(recached.fetchedAt, mixed.fetchedAt)
  assert.equal(recached.expiresAt, mixed.expiresAt)
  assert.equal(recached.expired, true)
})

test('Every restoration has fresh native source IDs and cache flags while preserving original direct provenance and body', () => {
  const { cache } = fixture(), original = [source(1), source(2, { publishedAt: null, truncated: true })]
  const entry = put(cache, 'two', original), first = cache.restore(entry.id, false), second = cache.restore(entry.id, false)
  assert.equal(new Set([...original, ...first, ...second].map(item => item.id)).size, 6)
  for (let index = 0; index < original.length; index += 1) {
    assert.match(first[index].id, /^internet_[0-9a-f]{32}$/u)
    assert.deepEqual({ ...first[index], id: original[index].id, fromCache: false }, original[index])
    assert.equal(first[index].fromCache, true)
    assert.equal(second[index].fetchedAt, original[index].fetchedAt)
  }
})

test('Structured weather and GitHub evidence uses the shared strict source parser and retains its five-minute age on restore', () => {
  const { cache, clock } = fixture()
  for (const provider of ['weather', 'github']) {
    const original = source(provider === 'weather' ? 1 : 2, { provider, publishedAt: null,
      modifiedAt: provider === 'weather' ? null : iso(at - 60000) })
    const entry = put(cache, provider, [original], INTERNET_CACHE_SHORT_TTL_MS)
    assert.equal(entry.expiresAt, iso(at + 300000))
    const restored = cache.restore(entry.id, false)[0]
    assert.equal(restored.provider, provider)
    assert.equal(restored.fetchedAt, original.fetchedAt)
    assert.equal(restored.modifiedAt, original.modifiedAt)
    assert.equal(restored.fromCache, true)
  }
  clock.value += 300000
  assert.ok(cache.list().every(entry => entry.expired))
})

test('Caller mutations of inserted sources, returned metadata, lists or restored evidence never alter native cache contents', () => {
  const { cache } = fixture(), original = source(), expected = structuredClone(original), entry = put(cache, 'detached', [original])
  original.title = 'mutated input'; original.content = 'mutated body'; original.fetchedAt = iso(at + 5000)
  const nativeId = entry.id
  entry.label = 'mutated metadata'; entry.expiresAt = iso(at)
  const listed = cache.list(); listed[0].label = 'mutated list'; listed.length = 0
  const restored = cache.restore(nativeId, false)
  assert.deepEqual({ ...restored[0], id: expected.id, fromCache: false }, expected)
  restored[0].content = 'mutated restore'; restored.push(source(7))
  assert.equal(cache.restore(nativeId, false).length, 1)
  assert.equal(cache.restore(nativeId, false)[0].content, expected.content)
  assert.equal(cache.list()[0].label, 'Label detached')
})

test('Replacing the same native key gives a fresh cache identity and invalidates the old receipt without adding another entry', () => {
  const { cache } = fixture(), first = put(cache, 'same', [source(1)]), second = put(cache, 'same', [source(2)])
  assert.notEqual(first.id, second.id)
  assert.equal(cache.list().length, 1)
  assert.throws(() => cache.restore(first.id, false), code('not_found'))
  assert.equal(cache.restore(second.id, false)[0].url, source(2).url)
})

test('Twenty-entry eviction uses successful restores and replacements as access, while list polling does not change recency', () => {
  const { cache } = fixture(), entries = []
  for (let index = 0; index < 20; index += 1) entries.push(put(cache, `key${index}`, [], INTERNET_CACHE_SHORT_TTL_MS))
  assert.equal(cache.list().length, 20)
  assert.deepEqual(cache.list().map(item => item.id), entries.map(item => item.id).reverse())
  cache.restore(entries[0].id, false)
  cache.list(); cache.list()
  const newest = put(cache, 'key20', [], INTERNET_CACHE_SHORT_TTL_MS)
  assert.equal(cache.list().length, 20)
  assert.equal(cache.list()[0].id, newest.id)
  assert.equal(cache.list()[1].id, entries[0].id)
  assert.throws(() => cache.restore(entries[1].id, false), code('not_found'))
  assert.equal(cache.list().at(-1).id, entries[2].id)
})

test('The two-MiB budget counts actual UTF-8 JSON, including Unicode, escapes, private keys, provenance and array punctuation', () => {
  for (const content of ['🌍'.repeat(8192), '\n'.repeat(32768)]) {
    const { cache } = fixture(), expected = new Map()
    for (let index = 0; index < 15; index += 1) {
      const key = JSON.stringify({ kind: 'page', name: `quote"${index}`, unicode: '世界' }), label = `Entry ${index}`
      const sources = Array.from({ length: 8 }, (_, sourceIndex) => source(index * 8 + sourceIndex + 1, { content }))
      const entry = cache.put(key, label, sources, INTERNET_CACHE_WEB_TTL_MS)
      expected.set(key, { id: entry.id, key, label, sources, fetchedAt: entry.fetchedAt, expiresAt: entry.expiresAt })
      while (expected.size > 20 || Buffer.byteLength(JSON.stringify([...expected.values()]), 'utf8') > 2 * 1024 * 1024) {
        expected.delete(expected.keys().next().value)
      }
      assert.deepEqual(cache.list().map(item => item.id), [...expected.values()].reverse().map(item => item.id))
    }
    assert.ok(expected.size > 0 && expected.size < 15)
    assert.ok(Buffer.byteLength(JSON.stringify([...expected.values()]), 'utf8') <= 2 * 1024 * 1024)
  }
})

test('Malformed, excessive or failed-response-shaped entries cannot replace good evidence or evict it during validation', () => {
  const { cache } = fixture(), original = put(cache)
  for (const [key, label, sources, ttl, expected] of [
    ['', 'Label', [source()], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['bad\nkey', 'Label', [source()], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['x'.repeat(4097), 'Label', [source()], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['native-key', '', [source()], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['native-key', 'Label', [source()], 1, 'invalid_request'],
    ['native-key', 'Label', [source(1, { provider: 'wikipedia' })], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['native-key', 'Label', [], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['native-key', 'Label', [source(1), source(1)], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['native-key', 'Label', [source(1, { content: 'x'.repeat(32769) })], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['native-key', 'Label', [source(1, { error: 'failed response' })], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['native-key', 'Label', [source(1, { provider: 'arbitrary-provider' })], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['native-key', 'Label', [source(1, { url: 'https://secret:password@example.com/' })], INTERNET_CACHE_WEB_TTL_MS, 'invalid_request'],
    ['native-key', 'Label', Array.from({ length: 11 }, (_, index) => source(index + 1)), INTERNET_CACHE_WEB_TTL_MS, 'limit_exceeded'],
  ]) {
    assert.throws(() => cache.put(key, label, sources, ttl), code(expected))
    assert.deepEqual(cache.list(), [original])
  }
  assert.equal(cache.restore(original.id, false)[0].content, source().content)
})

test('Absent and malformed restore identities fail safely, and unsuccessful expired lookup does not gain LRU priority', () => {
  const { cache, clock } = fixture(), stale = put(cache, 'stale', [], INTERNET_CACHE_SHORT_TTL_MS)
  const fresh = put(cache, 'fresh')
  assert.throws(() => cache.restore('not-an-id', false), code('invalid_request'))
  assert.throws(() => cache.restore(`internet_cache_${'0'.repeat(32)}`, false), code('not_found'))
  assert.throws(() => cache.restore(fresh.id, 'true'), code('invalid_request'))
  clock.value = at + 300000
  assert.throws(() => cache.restore(stale.id, false), code('expired'))
  assert.deepEqual(cache.list().map(item => item.id), [fresh.id, stale.id])
})

test('Bad clocks fail with fixed errors and clear remains idempotent even when time is unavailable', () => {
  for (const clock of [() => NaN, () => Infinity, () => 1.5, () => Number.MAX_SAFE_INTEGER, () => { throw new Error('private clock diagnostic') }]) {
    const cache = new InternetCacheStore(clock)
    assert.throws(() => put(cache), code('invalid_request'))
    assert.throws(() => cache.list(), code('invalid_request'))
    cache.clear(); cache.clear()
  }
  const { cache } = fixture(), entry = put(cache)
  cache.clear(); cache.clear()
  assert.deepEqual(cache.list(), [])
  assert.throws(() => cache.restore(entry.id, true), code('not_found'))
  assert.equal(put(cache).sourceCount, 1)
})

test('Offline put, list, restore and clear need no network service or background refresh', () => {
  const savedFetch = globalThis.fetch
  let networkCalls = 0
  globalThis.fetch = () => { networkCalls += 1; throw new Error('No network allowed') }
  try {
    const { cache, clock } = fixture(), entry = put(cache)
    assert.equal(cache.list().length, 1)
    assert.equal(cache.restore(entry.id, false)[0].fromCache, true)
    clock.value += 1800000
    assert.equal(cache.restore(entry.id, true)[0].fetchedAt, iso(at))
    cache.clear()
    assert.equal(networkCalls, 0)
  } finally { globalThis.fetch = savedFetch }
})
