/** Verify fixed no-key search endpoints, inert bounded excerpts, encyclopedia fallback, and cancellation. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { parseChatWebSearch, searchChatInternet } from '../dist-electron/internet-chat-search.js'

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

test('uses one bounded fixed web request, never requests source destinations', async () => {
  const calls = []
  const transport = { read: async (url, options) => { calls.push({ url, options }); return response(result('https://example.org/', 'Source', 'Evidence')) } }
  assert.equal((await searchChatInternet(transport, request, new AbortController().signal))[0].provider, 'web')
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
})

test('cancellation cannot start a fallback after a revoked web request', async () => {
  const controller = new AbortController()
  let count = 0
  const transport = { read: async () => { count++; controller.abort(); throw new Error('cancelled') } }
  await assert.rejects(searchChatInternet(transport, request, controller.signal))
  assert.equal(count, 1)
})
