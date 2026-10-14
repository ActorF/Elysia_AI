/** Verify source comparisons distinguish dates and inference, and reject expired, replaced or cross-scope draft receipts. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { InternetReviewStore } from '../dist-electron/internet-review.js'
import { parseInternetReviewRequest, parseInternetReviewText } from '../dist-electron/internet-review-protocol.js'

const owner = { chatId: 'chat_review', projectId: null }
const at = Date.parse('2026-10-14T18:00:00.000Z')
function source(number = 1) {
  return { id: `internet_${String(number).padStart(32, '0')}`, title: `Source ${number}`, url: `https://en.wikipedia.org/?curid=${number}`,
    snippet: 'A reported claim, not independently verified.', content: null, publishedAt: null,
    modifiedAt: '2026-10-13T12:00:00.000Z', fetchedAt: new Date(at).toISOString(), provider: 'wikipedia', fromCache: false, truncated: false }
}
function request(sources = [source()]) {
  return { chatId: owner.chatId, sourceIds: sources.map(item => item.id), claim: 'What changed?', interpretation: 'source_report', eventDate: null, uncertainty: '' }
}

test('comparison exposes unknown publication, shared domains and a separately labeled inferred event date', () => {
  const sources = [source(), source(2)], store = new InternetReviewStore(() => at)
  const input = { ...request(sources), interpretation: 'inference', eventDate: '2026-10-12' }
  const lookup = id => sources.find(item => item.id === id)
  const review = store.prepare(owner, input, lookup)
  assert.ok(review.warnings.some(value => value.includes('share a domain')))
  assert.ok(review.warnings.some(value => value.includes('publication date is unknown')))
  assert.ok(review.warnings.some(value => value.includes('inference')))
  const result = parseInternetReviewText(store.resolve(owner, { chatId: owner.chatId, reviewId: review.id }, lookup))
  assert.match(result.text, /userSuppliedEventDate/u)
  assert.match(result.text, /2026-10-12/u)
  assert.match(result.text, /https:\/\/en.wikipedia.org\/\?curid=1/u)
  assert.match(result.text, /Not independently verified/u)
  assert.throws(() => parseInternetReviewText({ ...result, text: result.text + ' changed' }))
})

test('scope changes, source mutations, replacement, expiry and explicit clear invalidate a receipt', () => {
  let now = at, item = source()
  const store = new InternetReviewStore(() => now), lookup = () => item
  let review = store.prepare(owner, request(), lookup)
  const resolve = () => store.resolve(owner, { chatId: owner.chatId, reviewId: review.id }, lookup)
  assert.throws(() => store.resolve({ ...owner, projectId: 'project_changed' }, { chatId: owner.chatId, reviewId: review.id }, lookup))
  item = { ...item, snippet: 'Replaced text' }
  assert.throws(resolve)
  review = store.prepare(owner, request(), lookup)
  now += 300_000
  assert.throws(resolve)
  now -= 300_000
  assert.throws(resolve)
  review = store.prepare(owner, request(), lookup)
  store.prepare(owner, request(), lookup)
  assert.throws(resolve)
  review = store.prepare(owner, request(), lookup)
  store.clear()
  assert.throws(resolve)
})

test('comparison enforces distinct bounded identities, calendar dates and exact fields', () => {
  assert.throws(() => parseInternetReviewRequest({ ...request(), sourceIds: [] }))
  assert.throws(() => parseInternetReviewRequest({ ...request(), sourceIds: [source().id, source().id] }))
  assert.throws(() => parseInternetReviewRequest({ ...request(), eventDate: '2026-02-30' }))
  assert.throws(() => parseInternetReviewRequest({ ...request(), authorization: true }))
  assert.throws(() => parseInternetReviewRequest({ ...request(), uncertainty: '秘'.repeat(1001) }))
  const store = new InternetReviewStore(() => at)
  const item = { ...source(), content: '文'.repeat(10000), fromCache: true }
  const review = store.prepare(owner, request(), () => item)
  const result = store.resolve(owner, { chatId: owner.chatId, reviewId: review.id }, () => item)
  assert.ok(review.warnings.some(value => value.includes('Cached')))
  assert.ok(result.text.length < 5000)
  assert.match(result.text, /"truncated": true/u)
})

test('publication relative to the user event date remains distinct from modification and retrieval', () => {
  const store = new InternetReviewStore(() => at)
  const early = { ...source(), publishedAt: '2026-10-10T00:00:00.000Z' }
  const review = store.prepare(owner, { ...request(), eventDate: '2026-10-12' }, () => early)
  assert.ok(review.warnings.some(value => value.includes('predate the supplied event')))
  const future = { ...source(), publishedAt: '2027-01-01T00:00:00.000Z' }
  const suspicious = store.prepare(owner, request(), () => future)
  assert.ok(suspicious.warnings.some(value => value.includes('later than retrieval')))
})
