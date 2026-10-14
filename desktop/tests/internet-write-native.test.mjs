/** Verify Main consumes launch secrets before child inheritance and requires an independent exact native Issue confirmation. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { confirmGithubIssueNative, takeInternetGithubToken } from '../dist-electron/internet-write-native.js'

test('launch credential is consumed without changing other environment settings or accepting invalid tokens', () => {
  const environment = { ELYSIA_GITHUB_TOKEN: 'test-only-token', KEEP: 'preserved' }
  assert.equal(takeInternetGithubToken(environment), 'test-only-token')
  assert.deepEqual(environment, { KEEP: 'preserved' })
  assert.equal(takeInternetGithubToken(environment), null)
  for (const token of ['', 'bad\nheader', 'has space', 'x'.repeat(4097)]) {
    const invalid = { ELYSIA_GITHUB_TOKEN: token }
    assert.equal(takeInternetGithubToken(invalid), null)
    assert.equal(Object.hasOwn(invalid, 'ELYSIA_GITHUB_TOKEN'), false)
  }
})

test('native Issue confirmation shows the exact target/content and defaults to Cancel', async () => {
  const preview = { id: `internet_approval_${'1'.repeat(32)}`, scope: { chatId: 'chat_write', projectId: null },
    owner: 'octocat', repo: 'Hello-World', title: 'Reviewed title', body: 'Line one\nLine two\u202e', expiresAt: new Date(Date.now() + 300000).toISOString() }
  let options
  assert.equal(await confirmGithubIssueNative(preview, async value => { options = value; return { response: 0 } }), false)
  assert.equal(options.defaultId, 0)
  assert.equal(options.cancelId, 0)
  assert.match(options.detail, /https:\/\/api.github.com\/repos\/octocat\/Hello-World\/issues/u)
  assert.match(options.detail, /Reviewed title/u)
  assert.ok(options.detail.includes('Line one\\nLine two\\u202e'))
  assert.ok(!options.detail.includes('\u202e'))
  assert.match(options.detail, /may notify/u)
  assert.match(options.detail, /cannot undo/u)
  assert.equal(await confirmGithubIssueNative(preview, async () => ({ response: 1 })), true)
  await assert.rejects(confirmGithubIssueNative({ ...preview, body: 'x'.repeat(4001) }, async () => { throw new Error('Must not show invalid data') }))
})
