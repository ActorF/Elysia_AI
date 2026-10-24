/** Verify fixed search endpoints, authorized inert page reading, honest fallback evidence, and cancellation. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { InternetChatSearchError, parseChatWebSearch, searchChatInternet } from '../dist-electron/internet-chat-search.js'

const fetchedAt = '2026-10-14T12:00:00.000Z'
const response = html => ({ status: 200, url: 'https://lite.duckduckgo.com/lite/?q=test', headers: {},
  fetchedAt, body: Buffer.from(html) })
const result = (url, title, snippet) => `<table><tr><td><a class='result-link' href='${url}'>${title}</a></td></tr><tr><td class='result-snippet'>${snippet}</td></tr></table>`
const request = { chatId: 'chat_test', query: 'test', language: 'en' }

test('unwraps only provider redirects locally and strips executable text without fetching result pages', () => {
  const sources = parseChatWebSearch(response(result('//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdoc%23section',
    'Python &amp; docs', 'Read <b>documentation</b><script>steal()</script>')))
  assert.deepEqual(sources, [{ title: 'Python & docs', url: 'https://www.python.org/doc',
    snippet: 'Read documentation', fetchedAt, provider: 'web' }])
})

test('rejects unsafe source URLs, duplicate results, challenge pages and oversized evidence', () => {
  const html = ['http://example.org', 'https://127.0.0.1/', 'https://localhost/', 'https://example.local/',
    'https://user:pass@example.org/'].map(url => result(url, 'Rejected', 'no')).join('')
    + Array.from({ length: 8 }, (_, index) => result(`https://example.org/${index}?q=${'a'.repeat(3500)}`,
      '标题'.repeat(900), '内容'.repeat(1500))).join('')
  const sources = parseChatWebSearch(response(html))
  assert.ok(sources.length > 0 && sources.length <= 5)
  assert.ok(Buffer.byteLength(JSON.stringify(sources)) <= 32768)
  assert.ok(sources.every(source => source.title !== 'Rejected'))
  assert.throws(() => parseChatWebSearch(response('<form id="challenge-form">captcha</form>')))
  assert.equal(parseChatWebSearch(response(result('https://example.org/', 'One', 'yes') + result('https://example.org/', 'Two', 'no'))).length, 1)
  assert.equal(parseChatWebSearch(response(result('https://example.org/', 'No evidence', ''))).length, 0)
})

test('uses one bounded fixed web request and never requests source destinations without an explicit allowed domain', async () => {
  const calls = []
  const transport = { read: async (url, options) => { calls.push({ url, options }); return response(result('https://example.org/', 'Source', 'Evidence')) } }
  const sources = await searchChatInternet(transport, request, new AbortController().signal)
  assert.equal(sources[0].provider, 'web')
  assert.match(sources[0].snippet, /page body was not read/u)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'https://lite.duckduckgo.com/lite/?q=test')
  assert.equal(calls[0].options.maxRedirects, 0)
})

test('falls back once to explicitly labeled encyclopedia excerpts when web search is unavailable', async () => {
  const calls = []
  const transport = { read: async (url) => {
    calls.push(url)
    if (calls.length === 1) return { ...response('challenge'), status: 202 }
    return { ...response(JSON.stringify({ query: { search: [{ pageid: 1, ns: 0, title: 'Article', snippet: 'An excerpt', timestamp: '2026-10-01T00:00:00Z' }] } })), url }
  } }
  const sources = await searchChatInternet(transport, request, new AbortController().signal)
  assert.equal(calls.length, 2)
  assert.ok(calls[1].startsWith('https://en.wikipedia.org/w/api.php?'))
  assert.equal(sources[0].provider, 'wikipedia')
  assert.match(sources[0].snippet, /does not verify live information/u)
})

test('cancellation cannot start a fallback after a revoked web request', async () => {
  const controller = new AbortController()
  let count = 0
  const transport = { read: async () => { count++; controller.abort(); throw new Error('cancelled') } }
  await assert.rejects(searchChatInternet(transport, request, controller.signal))
  assert.equal(count, 1)
})

test('reads at most two exact allowed domains, strips executable content, and preserves other snippets honestly', async () => {
  const calls = [], stages = []
  const html = result('https://allowed.org/one', 'One', 'Short one')
    + result('https://sub.allowed.org/two', 'Two', 'Short two')
    + result('https://allowed.org/three', 'Three', 'Short three')
    + result('https://allowed.org/four', 'Four', 'Short four')
  const transport = { read: async (url, options) => {
    calls.push({ url, options })
    if (calls.length === 1) return response(html)
    return { ...response('<html><title>Page</title><article>Longer evidence<script>steal()</script><iframe src="https://evil.org"></iframe></article></html>'),
      url, headers: { 'content-type': 'text/html; charset=utf-8' } }
  } }
  const sources = await searchChatInternet(transport, { ...request, allowedDomains: ['allowed.org'] }, new AbortController().signal,
    () => stages.push('reading'))
  assert.deepEqual(calls.map(call => call.url), ['https://lite.duckduckgo.com/lite/?q=test', 'https://allowed.org/one', 'https://allowed.org/three'])
  assert.deepEqual(stages, ['reading'])
  assert.equal(calls[1].options.maxBytes, 1024 * 1024)
  assert.equal(calls[1].options.maxRedirects, 3)
  assert.match(sources[0].snippet, /Page text excerpt.*Longer evidence/u)
  assert.equal(sources[0].title, 'Page')
  assert.doesNotMatch(sources[0].snippet, /steal|evil\.org/u)
  assert.match(sources[1].snippet, /page body was not read.*Short two/u)
  assert.match(sources[3].snippet, /page body was not read.*Short four/u)
})

test('page failures preserve actual search evidence without claiming the page was read', async () => {
  let calls = 0
  const transport = { read: async () => {
    if (++calls === 1) return response(result('https://allowed.org/', 'One', 'Verified search excerpt'))
    throw new Error('domain_denied after redirect')
  } }
  const sources = await searchChatInternet(transport, { ...request, allowedDomains: ['allowed.org'] }, new AbortController().signal)
  assert.equal(calls, 2)
  assert.match(sources[0].snippet, /page body was not read.*Verified search excerpt/u)
})

test('cancelled page reading cannot publish partial evidence or start another page or fallback', async () => {
  let calls = 0
  const controller = new AbortController()
  const transport = { read: async url => {
    if (++calls === 1) return response(result('https://allowed.org/one', 'One', 'Excerpt') + result('https://allowed.org/two', 'Two', 'Excerpt'))
    controller.abort()
    return { ...response('Too late'), url, headers: { 'content-type': 'text/plain' } }
  } }
  await assert.rejects(searchChatInternet(transport, { ...request, allowedDomains: ['allowed.org'] }, controller.signal), /cancelled/u)
  assert.equal(calls, 2)
})

test('already cancelled lookup starts no network request and does not report a reading stage', async () => {
  const controller = new AbortController(); controller.abort()
  let calls = 0
  await assert.rejects(searchChatInternet({ read: async () => { calls++ } }, request, controller.signal), /cancelled/u)
  assert.equal(calls, 0)
})

test('a failed primary and empty encyclopedia fallback retain both safe failure categories', async () => {
  let calls = 0
  const transport = { read: async url => {
    if (++calls === 1) return response('<form id="challenge-form">Blocked</form>')
    return { ...response(JSON.stringify({ query: { search: [] } })), url }
  } }
  await assert.rejects(searchChatInternet(transport, request, new AbortController().signal), error =>
    error instanceof InternetChatSearchError && error.primaryCode === 'challenge' && error.fallbackCode === 'no_results')
  assert.equal(calls, 2)
})

test('bad provider HTML remains an explicit failed search when the fallback also fails', async () => {
  let calls = 0
  const transport = { read: async () => {
    if (++calls === 1) return { ...response(''), body: Buffer.from([0xff]) }
    throw new Error('native socket details must not escape')
  } }
  await assert.rejects(searchChatInternet(transport, request, new AbortController().signal), error =>
    error instanceof InternetChatSearchError && error.primaryCode === 'invalid_response'
      && error.fallbackCode === 'unavailable' && !error.message.includes('socket'))
})

test('enrichment stays inside per-source and total budgets and deduplicates redirected pages', async () => {
  let calls = 0
  const transport = { read: async () => {
    if (++calls === 1) return response(result('https://allowed.org/one', 'One', 'Excerpt') + result('https://allowed.org/two', 'Two', 'Excerpt'))
    return { ...response('正文'.repeat(10000)), url: 'https://allowed.org/final', headers: { 'content-type': 'text/plain' } }
  } }
  const sources = await searchChatInternet(transport, { ...request, language: 'zh', allowedDomains: ['allowed.org'] }, new AbortController().signal)
  assert.equal(sources.length, 1)
  assert.equal(sources[0].url, 'https://allowed.org/final')
  assert.ok(Buffer.byteLength(sources[0].snippet) <= 4096)
  assert.ok(Buffer.byteLength(JSON.stringify(sources)) <= 32768)
  assert.doesNotMatch(sources[0].snippet, /\ufffd/u)
})
