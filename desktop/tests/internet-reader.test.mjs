/** Verify inert page extraction, claimed dates, Unicode bounds and shared transport ownership. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { InternetReaderError, readInternetPage } from '../dist-electron/internet-reader.js'

function response(body, overrides = {}) {
  return { url: 'https://example.org/article', status: 200,
    headers: { 'content-type': 'text/html; charset=utf-8' },
    body: typeof body === 'string' ? Buffer.from(body, 'utf8') : body,
    fetchedAt: '2026-10-14T12:00:00.000Z', ...overrides }
}
async function read(body, overrides = {}) {
  return readInternetPage({ read: async () => response(body, overrides) },
    'https://example.org/article', new AbortController().signal)
}
function error(code) { return value => value instanceof InternetReaderError && value.code === code }

test('Reader makes one bounded policy-controlled read and returns the final redirected source', async () => {
  const calls = [], signal = new AbortController().signal
  const source = await readInternetPage({ read: async (url, options) => {
    calls.push({ url, options })
    return response(`<!doctype html><title>Report &amp; update</title>
      <link rel="stylesheet" href="https://other.net/style">
      <body><article><h1>Report</h1><p>Public evidence.</p>
      <img src="https://other.net/pixels"><iframe src="https://other.net/frame">secret</iframe>
      <script src="https://other.net/script">throw new Error('executed')</script></article></body>`,
    { url: 'https://example.org/final' })
  } }, 'https://example.org/start', signal)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'https://example.org/start')
  assert.deepEqual(calls[0].options, { accept: 'text/html, text/plain', maxBytes: 1024 * 1024, maxRedirects: 3, signal })
  assert.equal(source.url, 'https://example.org/final')
  assert.equal(source.title, 'Report & update')
  assert.equal(source.content, 'Report\n\nPublic evidence.')
  assert.match(source.id, /^internet_[0-9a-f]{32}$/u)
  assert.equal(source.provider, 'web')
  assert.equal(source.fromCache, false)
  assert.equal(source.truncated, false)
})

test('Semantic article content wins over page chrome and hidden/actionable trees', async () => {
  const source = await read(`<html><body>outside<main>main introduction<article>
    <header>header secret</header><nav>nav secret</nav><form>form secret</form>
    <style>style secret</style><script>script secret</script><template>template secret</template>
    <noscript>noscript secret</noscript><footer>footer secret</footer>
    <div hidden>hidden secret</div><div aria-hidden="true">aria secret</div>
    <div inert>inert secret</div><div style="display: none !important">css secret</div>
    <div style="visibility: hidden;">visibility secret</div>
    <svg><text>svg secret</text></svg><object>object secret</object>
    <p>Hello &amp; <strong>world</strong>.</p><p>Second line.</p>
    </article></main></body></html>`)
  assert.equal(source.content, 'Hello & world.\n\nSecond line.')
  assert.equal(source.snippet, 'Hello & world. Second line.')
  assert.equal(source.title, 'example.org')
})

test('Reader falls back to main and body, handles malformed HTML and never includes comments', async () => {
  const main = await read('<body>outside<main><p>first<p>second<!--private--></main>outside')
  assert.equal(main.content, 'first\n\nsecond')
  const body = await read('<body><nav>menu</nav><p>ordinary body</p><footer>footer</footer></body>')
  assert.equal(body.content, 'ordinary body')
  const emptyArticle = await read('<main><article><script>no visible content</script></article><p>main fallback</p></main>')
  assert.equal(emptyArticle.content, 'main fallback')
})

test('Explicit page publication and modification claims stay distinct from retrieval and HTTP dates', async () => {
  const source = await read(`<head>
    <meta property="article:published_time" content="2026-10-10T12:30:00+08:00">
    <meta property="article:modified_time" content="2026-10-12T14:00:00Z">
    </head><body><p>Event mentioned on 2020-01-01.</p></body>`, {
    headers: { 'content-type': 'text/html', 'last-modified': 'Wed, 14 Oct 2026 15:00:00 GMT',
      date: 'Wed, 14 Oct 2026 16:00:00 GMT' },
  })
  assert.equal(source.publishedAt, '2026-10-10T04:30:00.000Z')
  assert.equal(source.modifiedAt, '2026-10-12T14:00:00.000Z')
  assert.equal(source.fetchedAt, '2026-10-14T12:00:00.000Z')
  const headerOnly = await read('<p>Useful body.</p>', {
    headers: { 'content-type': 'text/html', 'last-modified': 'Wed, 14 Oct 2026 15:00:00 GMT' },
  })
  assert.equal(headerOnly.publishedAt, null)
  assert.equal(headerOnly.modifiedAt, '2026-10-14T15:00:00.000Z')
})

test('Invalid, ambiguous, injected and event-looking timestamps never become publication evidence', async () => {
  for (const date of ['2026-02-30T00:00:00Z', '2026-13-01T00:00:00Z', '2026-01-01T24:00:00Z',
    '2026-10-01', '2026-10-01 12:00:00', 'Wed, 14 Oct 2026 15:00:00 GMT', '2026-10-01T12:00:00Z private']) {
    const source = await read(`<head><meta property="article:published_time" content="${date}"></head><p>Body.</p>`)
    assert.equal(source.publishedAt, null, date)
  }
  const conflicting = await read(`<head>
    <meta property="article:published_time" content="2026-10-01T12:00:00Z">
    <meta name="datepublished" content="2026-10-02T12:00:00Z">
    </head><p>Published <time datetime="2020-01-01T00:00:00Z">a date</time></p>`)
  assert.equal(conflicting.publishedAt, null)
  for (const date of ['Thu, 14 Oct 2026 15:00:00 GMT', '2026-10-14', 'Wed, 14 Oct 2026 15:00:00 GMT\r\nInjected: yes']) {
    const source = await read('<p>Body.</p>', { headers: { 'content-type': 'text/html', 'last-modified': date } })
    assert.equal(source.modifiedAt, null)
    assert.equal(source.publishedAt, null)
  }
})

test('Plain text stays literal and strips unsupported control bytes without HTML interpretation', async () => {
  const source = await read('第一行\r\n<script>literal text</script>\u0000safe', {
    headers: { 'content-type': 'text/plain; charset="UTF-8"' },
  })
  assert.equal(source.content, '第一行\n<script>literal text</script> safe')
  assert.equal(source.publishedAt, null)
  assert.equal(source.modifiedAt, null)
})

test('Unsupported content types and non-UTF8 declarations or bytes fail without partial source output', async () => {
  for (const type of ['application/pdf', 'application/json', 'image/png', 'application/xhtml+xml', '', undefined]) {
    await assert.rejects(read('<p>body</p>', { headers: { 'content-type': type } }), error('unsupported_type'))
  }
  for (const type of ['text/html; charset=gbk', 'text/plain; charset=iso-8859-1',
    'text/html; charset=utf-8; charset=windows-1252', 'text/html; charset="utf-8" private']) {
    await assert.rejects(read('<p>body</p>', { headers: { 'content-type': type } }), error('invalid_encoding'))
  }
  for (const html of ['<meta charset="gbk"><p>body</p>',
    '<meta http-equiv="content-type" content="text/html; charset=iso-8859-1"><p>body</p>']) {
    await assert.rejects(read(html), error('invalid_encoding'))
  }
  await assert.rejects(read(Buffer.from([0xc3, 0x28])), error('invalid_encoding'))
})

test('Extracted content, snippets and titles use byte limits without splitting multibyte characters', async () => {
  const title = '中文😀'.repeat(1000), paragraph = '證據😀'.repeat(10_000)
  const source = await read(`<title>${title}</title><article>${paragraph}</article>`)
  assert.equal(source.truncated, true)
  assert.ok(Buffer.byteLength(source.content, 'utf8') <= 32768)
  assert.ok(Buffer.byteLength(source.snippet, 'utf8') <= 2048)
  assert.ok(Buffer.byteLength(source.title, 'utf8') <= 2048)
  assert.ok(paragraph.startsWith(source.content))
  assert.ok(title.startsWith(source.title))
  assert.ok(![source.content, source.snippet, source.title].some(value => value.includes('\ufffd')))
})

test('Oversized bodies, nonfinal responses, empty content and invalid source identity fail closed', async () => {
  await assert.rejects(read('x'.repeat(1024 * 1024 + 1)), error('limit_exceeded'))
  for (const status of [204, 302, 403, 404, 500]) {
    await assert.rejects(read('body', { status }), error('invalid_document'))
  }
  for (const html of ['', '<script>only script</script>', '<nav>only navigation</nav>']) {
    await assert.rejects(read(html), error('empty_document'))
  }
  for (const url of ['https://user:secret@example.org/', 'http://example.org/', 'https://example.org/#fragment', 'invalid']) {
    await assert.rejects(read('<p>body</p>', { url }), error('invalid_document'))
  }
  await assert.rejects(read('<p>body</p>', { fetchedAt: '2026-02-30T00:00:00Z' }), error('invalid_document'))
})

test('A small-byte but excessive-node document is rejected before evidence publication', async () => {
  await assert.rejects(read('<p>x</p>'.repeat(50_001)), error('limit_exceeded'))
})

test('Cancellation wins both before admission and after a pending transport resolves', async () => {
  const abort = new AbortController()
  abort.abort()
  let calls = 0
  await assert.rejects(readInternetPage({ read: async () => { calls += 1 } }, 'https://example.org/', abort.signal), error('cancelled'))
  assert.equal(calls, 0)
  const active = new AbortController()
  let finish
  const pending = readInternetPage({ read: async () => new Promise(resolve => { finish = resolve }) },
    'https://example.org/', active.signal)
  active.abort()
  finish(response('<p>Late response</p>'))
  await assert.rejects(pending, error('cancelled'))
})

test('Transport policy and redirect failures remain intact rather than becoming fabricated page sources', async () => {
  const failure = new Error('controlled transport failure')
  await assert.rejects(readInternetPage({ read: async () => { throw failure } },
    'https://example.org/', new AbortController().signal), value => value === failure)
})
