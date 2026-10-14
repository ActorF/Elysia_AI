/** Verify the packaging downloader's compatible proxy bootstrap after removing vulnerable logging dependencies. */

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

test('packaging downloader initializes its scoped proxy agent without the removed formatter', () => {
  // Proxy bootstrap replaces global HTTP agents. An isolated child exercises
  // the real packaging dependency without altering other tests or contacting
  // a server; the deliberately unused local proxy verifies configuration.
  const script = `
    import assert from 'node:assert/strict'
    import { createRequire } from 'node:module'
    const localRequire = createRequire(import.meta.url)
    const builderRequire = createRequire(localRequire.resolve('app-builder-lib/package.json'))
    const downloader = builderRequire('@electron/get')
    await downloader.initializeProxy()
    assert.equal(globalThis.GLOBAL_AGENT.HTTP_PROXY, 'http://127.0.0.1:9')
    assert.equal(globalThis.GLOBAL_AGENT.HTTPS_PROXY, 'http://127.0.0.1:9')
  `
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    cwd: fileURLToPath(new URL('..', import.meta.url)), encoding: 'utf8', timeout: 10_000,
    env: { ...process.env, GLOBAL_AGENT_HTTP_PROXY: 'http://127.0.0.1:9',
      GLOBAL_AGENT_HTTPS_PROXY: 'http://127.0.0.1:9', GLOBAL_AGENT_NO_PROXY: '' },
  })
  assert.equal(result.status, 0, result.stderr)
})
