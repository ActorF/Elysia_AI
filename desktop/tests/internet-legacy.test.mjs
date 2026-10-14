/** Verify explicit browser handoffs and legacy lyrics cannot bypass the shared Internet policy or its revocation. */

import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { InternetPolicyService } from '../dist-electron/internet-policy.js'
import { assertInternetLyricsAllowed, openInternetBrowser, prepareInternetLyrics } from '../dist-electron/internet-legacy.js'

async function fixture(t) {
  const directory = await mkdtemp(path.join(tmpdir(), 'elysia-internet-legacy-'))
  const policy = new InternetPolicyService(path.join(directory, 'policy.json'))
  await policy.load()
  t.after(async () => {
    policy.shutdown()
    await policy.whenIdle()
    assert.equal(path.dirname(directory), path.resolve(tmpdir()))
    assert.ok(path.basename(directory).startsWith('elysia-internet-legacy-'))
    await rm(directory, { recursive: true, force: true })
  })
  const update = async (enabled, allowedDomains = ['lrclib.net', 'example.org']) => policy.update({
    expectedRevision: policy.getState().revision, enabled, allowedDomains, proxy: { mode: 'direct' }, timeoutMs: 15000,
  })
  return { policy, update }
}

test('Default-off denies browser confirmation and lyrics before a native callback or file probe', async (t) => {
  const { policy } = await fixture(t)
  let callbacks = 0
  await assert.rejects(openInternetBrowser(policy, 'https://example.org/', async () => { callbacks += 1; return true },
    async () => { callbacks += 1 }))
  assert.throws(() => assertInternetLyricsAllowed(policy))
  await assert.rejects(prepareInternetLyrics(policy, { read: async () => { callbacks += 1 } }, {
    signal: new AbortController().signal,
  }))
  assert.equal(callbacks, 0)
  assert.equal(policy.hasActiveWork(), false)
})

test('Browser handoff requires a public HTTPS URL and independent approval without custom domain setup', async (t) => {
  const { policy, update } = await fixture(t)
  await update(true)
  const opened = [], reviewed = []
  const open = async url => { opened.push(url) }
  const confirm = async url => { reviewed.push(url); return true }
  for (const url of ['http://example.org/', 'https://localhost/', 'https://name:password@example.org/',
    'https://example.org:8443/', 'https://example.org/#private', 'file:///private']) {
    await assert.rejects(openInternetBrowser(policy, url, confirm, open))
  }
  assert.deepEqual(reviewed, [])
  await openInternetBrowser(policy, 'https://example.org/article?version=2', async () => false, open)
  assert.deepEqual(opened, [])
  await openInternetBrowser(policy, 'https://example.org/article?version=2', confirm, open)
  assert.deepEqual(opened, ['https://example.org/article?version=2'])
  assert.deepEqual(reviewed, opened)
  await openInternetBrowser(policy, 'https://other.org/article', confirm, open)
  assert.equal(opened.at(-1), 'https://other.org/article')
  assert.equal(policy.getState().allowedDomains.includes('other.org'), false)
})

test('Turning off while native approval is open revokes the handoff and retains the lease until that dialog settles', async (t) => {
  const { policy, update } = await fixture(t)
  await update(true)
  let resolveApproval
  const approval = new Promise(resolve => { resolveApproval = resolve })
  let opened = false
  const pending = openInternetBrowser(policy, 'https://example.org/', () => approval, async () => { opened = true })
  const rejected = assert.rejects(pending)
  assert.equal(policy.hasActiveWork(), true)
  await update(false)
  assert.equal(policy.hasActiveWork(), true)
  resolveApproval(true)
  await rejected
  assert.equal(opened, false)
  assert.equal(policy.hasActiveWork(), false)
})

test('Built-in lyrics need no custom domain and a prior policy generation cannot regain permission after off and on', async (t) => {
  const { policy, update } = await fixture(t)
  await update(true, ['example.org'])
  assert.doesNotThrow(() => assertInternetLyricsAllowed(policy))
  await update(true)
  assert.doesNotThrow(() => assertInternetLyricsAllowed(policy))
  const lease = policy.acquire()
  await update(false)
  await update(true)
  assert.equal(lease.signal.aborted, true)
  assert.throws(() => lease.assertCurrent())
  lease.release()
  assert.equal(policy.hasActiveWork(), false)
})
