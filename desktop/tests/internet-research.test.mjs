/** Verify explicit encyclopedia search, inert provenance, scope revocation and physically drained Internet research ownership. */
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { InternetPolicyService } from '../dist-electron/internet-policy.js'
import { InternetResearchController } from '../dist-electron/internet-research.js'
import { parseWikipediaSearchResult, searchWikipedia } from '../dist-electron/internet-search.js'
import { formatInternetSource, parseInternetResearchState, parseInternetSearchRequest, parseInternetSourceText } from '../dist-electron/internet-protocol.js'

const fetchedAt = '2026-10-14T18:30:00.000Z'
const request = (query = 'Space telescope', language = 'en') => ({ chatId: 'chat_a', query, language })
const response = (pageid = 1, extra = {}) => ({ query: { search: [{ ns: 0, pageid, title: 'Telescope',
  snippet: 'A <span class="searchmatch">telescope</span> &amp; optics.', timestamp: '2026-10-13T10:00:00Z', ...extra }] } })
const result = (pageid = 1, language = 'en') => parseWikipediaSearchResult(response(pageid), language, fetchedAt)
const code = expected => error => error.code === expected
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function fixture(t, options = {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-internet-research-'))
  t.after(async () => {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()))
    assert.match(path.basename(directory), /^elysia-internet-research-/u)
    await rm(directory, { recursive: true, force: true })
  })
  const policy = new InternetPolicyService(path.join(directory, 'policy.json'))
  if (options.enabled !== false) await policy.update({ expectedRevision: 0, enabled: true,
    allowedDomains: ['en.wikipedia.org', 'zh.wikipedia.org'], proxy: { mode: 'direct' }, timeoutMs: 15000 })
  const owner = { chatId: 'chat_a', projectId: null, mode: 'chat', archived: false, ready: true, busy: false }
  const events = [], calls = []
  const controller = new InternetResearchController({ policy, getOwner: () => owner, now: () => new Date(fetchedAt),
    transport: { read: async () => { throw new Error('unexpected generic transport') } },
    onState: state => { events.push(state) }, search: async (value, signal) => {
      calls.push({ value, signal })
      return options.search === undefined ? result(calls.length, value.language) : options.search(value, signal)
    } })
  return { policy, owner, controller, events, calls }
}

test('Closed search and source-draft contracts reject scope hints, unknown fields and forged evidence text', () => {
  for (const changes of [{ projectId: 'project_other' }, { token: 'private' }, { query: ' ' }, { query: 'x'.repeat(513) },
    { query: 'text\u0000' }, { language: 'fr' }, { chatId: 'not_chat' }]) {
    assert.throws(() => parseInternetSearchRequest({ ...request(), ...changes }), TypeError)
  }
  const source = result().sources[0], text = formatInternetSource(source)
  assert.equal(parseInternetSourceText({ source, text }).text, text)
  assert.throws(() => parseInternetSourceText({ source, text: 'forged facts' }), TypeError)
  assert.match(text, /untrusted evidence/u)
  assert.match(text, /"publishedAt": "unknown"/u)
  assert.match(text, /"modifiedAt": "2026-10-13T10:00:00.000Z"/u)
  assert.doesNotMatch(text, /internet_[0-9a-f]{32}/u)
})

test('Wikipedia normalization preserves edit versus publication times, decodes full entities and never executes source markup', () => {
  const output = parseWikipediaSearchResult(response(123, { snippet:
    'Some <span title="a > b">science</span> &NotEqualTilde; &#x1F52D; &amp; &lt;tag&gt;'
      + '<script>leak()</script><style>private css</style><img src="https://evil.com/pixel"><iframe>private frame</iframe>' }), 'zh', fetchedAt)
  assert.equal(output.sources[0].snippet, 'Some science ≂̸ 🔭 & <tag>')
  assert.equal(output.sources[0].url, 'https://zh.wikipedia.org/?curid=123')
  assert.equal(output.sources[0].publishedAt, null)
  assert.equal(output.sources[0].modifiedAt, '2026-10-13T10:00:00.000Z')
  assert.equal(output.sources[0].content, null)
  assert.equal(output.sources[0].provider, 'wikipedia')
})

test('Wikipedia rejects malformed records and truncates snippets on UTF8 character boundaries', () => {
  for (const data of [{ error: { info: 'secret response' } }, response(0), response(1, { ns: 1 }),
    response(1, { timestamp: '2026-02-31T00:00:00Z' }), response(1, { title: '\u0001' }),
    response(1, { snippet: 'x'.repeat(16385) }), { query: { search: Array(6).fill(response().query.search[0]) } }]) {
    assert.throws(() => parseWikipediaSearchResult(data, 'en', fetchedAt), TypeError)
  }
  const source = parseWikipediaSearchResult(response(1, { snippet: '🌍'.repeat(1000) }), 'en', fetchedAt).sources[0]
  assert.equal(source.truncated, true)
  assert.equal(Buffer.byteLength(source.snippet, 'utf8'), 2048)
  assert.equal(source.snippet.endsWith('🌍'), true)
  assert.throws(() => parseWikipediaSearchResult({ query: { search: [] } }, 'en', 'bad time'), TypeError)
})

test('Provider makes exactly one credential-free fixed-language read and never follows pagination or redirects', async () => {
  const calls = [], signal = new AbortController().signal
  const transport = { read: async (url, settings) => {
    calls.push({ url, settings })
    return { url, status: 200, body: Buffer.from(JSON.stringify({ continue: { sroffset: 5 }, ...response() })), fetchedAt }
  } }
  const output = await searchWikipedia(transport, request('韦布望远镜', 'zh'), signal)
  assert.equal(output.sources.length, 1)
  assert.equal(calls.length, 1)
  const url = new URL(calls[0].url)
  assert.equal(url.origin, 'https://zh.wikipedia.org')
  assert.equal(url.pathname, '/w/api.php')
  assert.equal(url.searchParams.get('srsearch'), '韦布望远镜')
  assert.equal(url.searchParams.get('srlimit'), '5')
  assert.equal(url.searchParams.get('srnamespace'), '0')
  assert.equal(url.searchParams.get('srprop'), 'snippet|timestamp')
  assert.equal(calls[0].settings.maxRedirects, 0)
  assert.equal(calls[0].settings.maxBytes, 2 * 1024 * 1024)
  assert.equal(calls[0].settings.signal, signal)
})

test('Construction, state inspection and disabled searches produce no provider call', async t => {
  const { controller, policy, calls } = await fixture(t, { enabled: false })
  assert.deepEqual(controller.getState('chat_a').sources, [])
  assert.equal(controller.getState('chat_a').status, 'idle')
  await assert.rejects(controller.search(request()), code('disabled'))
  assert.equal(calls.length, 0)
  assert.equal(policy.hasActiveWork(), false)
})

test('Explicit search stores detached native sources/history and exports only owned source identities', async t => {
  const { controller, policy, calls } = await fixture(t)
  const state = await controller.search(request())
  assert.equal(calls.length, 1)
  assert.equal(state.status, 'idle')
  assert.equal(state.sources.length, 1)
  assert.deepEqual(state.history, [{ query: 'Space telescope', language: 'en', fetchedAt, count: 1, status: 'complete' }])
  state.sources[0].title = 'forged'
  const native = controller.getSource('chat_a', state.sources[0].id)
  assert.equal(native.title, 'Telescope')
  assert.equal(controller.sourceText({ chatId: 'chat_a', sourceId: native.id }).text, formatInternetSource(native))
  assert.throws(() => controller.getSource('chat_a', 'internet_' + '0'.repeat(32)), code('owner_changed'))
  assert.equal(policy.hasActiveWork(), false)
})

test('Session history stays at twenty and native sources at ten without writing query text to preferences', async t => {
  const { controller, policy } = await fixture(t)
  for (let index = 0; index < 24; index += 1) await controller.search(request(`query ${index}`))
  const state = controller.getState('chat_a')
  assert.equal(state.sources.length, 10)
  assert.equal(state.history.length, 20)
  assert.equal(state.history[0].query, 'query 23')
  assert.equal(state.history[19].query, 'query 4')
  assert.equal(JSON.stringify(policy.getState()).includes('query'), false)
  assert.throws(() => parseInternetResearchState({ ...state, sources: [...state.sources, state.sources[0]] }), TypeError)
})

test('Busy read-only Backend facts preserve existing evidence but reject a new search before provider admission', async t => {
  const { controller, owner, calls } = await fixture(t)
  await controller.search(request())
  owner.busy = true
  controller.synchronizeOwner()
  assert.equal(controller.getState('chat_a').sources.length, 1)
  await assert.rejects(controller.search(request()), code('busy'))
  assert.equal(calls.length, 1)
  owner.busy = false; owner.mode = 'work'
  controller.synchronizeOwner()
  assert.equal(controller.getState('chat_a').sources.length, 1)
})

test('A cancelled callback keeps busy and the long policy lease until its actual return, then records safe cancellation', async t => {
  const held = deferred()
  const { controller, policy, calls } = await fixture(t, { search: () => held.promise })
  const running = controller.search(request())
  assert.equal(calls.length, 1)
  assert.equal(policy.hasActiveWork(), true)
  assert.equal(controller.cancel('chat_a').busy, true)
  assert.equal(calls[0].signal.aborted, true)
  let idle = false
  const drained = controller.whenIdle().then(() => { idle = true })
  await Promise.resolve()
  assert.equal(idle, false)
  await assert.rejects(controller.search(request()), code('busy'))
  held.resolve(result())
  const state = await running
  await drained
  assert.equal(state.sources.length, 0)
  assert.equal(state.error, 'cancelled')
  assert.equal(state.history[0].status, 'cancelled')
  assert.equal(policy.hasActiveWork(), false)
})

test('Clear revokes late publication without claiming that outstanding network work already stopped', async t => {
  const held = deferred()
  const { controller, policy } = await fixture(t, { search: () => held.promise })
  const running = controller.search(request())
  const cleared = controller.clear('chat_a')
  assert.equal(cleared.busy, true)
  assert.equal(policy.hasActiveWork(), true)
  held.resolve(result())
  const state = await running
  assert.deepEqual(state.sources, [])
  assert.deepEqual(state.history, [])
  assert.equal(state.error, null)
})

test('Chat A to B to A and same-Chat Project changes cannot republish an old callback or its query', async t => {
  for (const change of ['chat', 'project', 'mode', 'disconnect', 'archive']) {
    const held = deferred()
    const { controller, owner, policy } = await fixture(t, { search: () => held.promise })
    const running = controller.search(request())
    if (change === 'chat') owner.chatId = 'chat_b'
    if (change === 'project') owner.projectId = 'project_b'
    if (change === 'mode') owner.mode = 'work'
    if (change === 'disconnect') owner.ready = false
    if (change === 'archive') owner.archived = true
    controller.synchronizeOwner()
    assert.equal(controller.hasActiveWork(), true)
    assert.equal(policy.hasActiveWork(), true)
    Object.assign(owner, { chatId: 'chat_a', projectId: null, mode: 'chat', ready: true, archived: false })
    controller.synchronizeOwner()
    held.resolve(result())
    const state = await running
    assert.deepEqual(state.sources, [], change)
    assert.deepEqual(state.history, [], change)
  }
})

test('Policy Off retains completed offline evidence but revokes pending result publication and its query', async t => {
  const held = deferred()
  let count = 0
  const { controller, policy } = await fixture(t, { search: () => ++count === 1 ? Promise.resolve(result()) : held.promise })
  await controller.search(request())
  const running = controller.search(request('next'))
  const state = policy.getState()
  const disabling = policy.update({ expectedRevision: state.revision, enabled: false, allowedDomains: state.allowedDomains,
    proxy: state.proxy, timeoutMs: state.timeoutMs })
  controller.synchronizeOwner()
  assert.equal(controller.getState('chat_a').sources.length, 1)
  assert.equal(controller.hasActiveWork(), true)
  await disabling
  held.resolve(result(2))
  const completed = await running
  assert.equal(completed.sources.length, 1)
  assert.equal(completed.history.length, 1)
  assert.equal(completed.history[0].query, 'Space telescope')
  assert.equal(controller.sourceText({ chatId: 'chat_a', sourceId: completed.sources[0].id }).source.title, 'Telescope')
  assert.equal(policy.hasActiveWork(), false)
})

test('Invalid provider results and private exceptions become fixed diagnostics without partial source publication', async t => {
  for (const provider of [async () => { throw new Error('private DNS 10.0.0.1 secret-token') },
    async () => ({ ...result(), sources: [{ ...result().sources[0], publishedAt: fetchedAt }] })]) {
    const { controller } = await fixture(t, { search: provider })
    const state = await controller.search(request())
    assert.equal(state.status, 'error')
    assert.deepEqual(state.sources, [])
    assert.equal(state.history[0].status, 'error')
    assert.doesNotMatch(JSON.stringify(state), /private DNS|secret-token|10\.0\.0\.1/u)
  }
})

test('Renderer invalidation and shutdown retain the callback until drain and cannot publish its late source', async t => {
  for (const closed of [false, true]) {
    const held = deferred()
    const { controller, policy } = await fixture(t, { search: () => held.promise })
    const running = controller.search(request())
    const finish = closed ? assert.rejects(running, code('unavailable')) : running
    if (closed) controller.shutdown(); else controller.invalidate()
    assert.equal(controller.hasActiveWork(), true)
    held.resolve(result())
    await finish; await controller.whenIdle()
    assert.equal(policy.hasActiveWork(), false)
    if (!closed) assert.deepEqual(controller.getState('chat_a').sources, [])
  }
})
