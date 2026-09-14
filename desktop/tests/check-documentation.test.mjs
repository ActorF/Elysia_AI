/**
 * @fileoverview Verifies maintained-source discovery for the documentation audit.
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { collectFiles } from '../scripts/check-documentation.mjs'

/** Create one source fixture and all of its parent directories. */
function createSource(root, relativePath) {
  const target = path.join(root, relativePath)
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.writeFileSync(target, '/** Maintained fixture. */\n', 'utf8')
  return target
}

test('source discovery prunes only the external model runtime cache', (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'elysia-doc-discovery-'))
  context.after(() => {
    // mkdtemp returns a unique child of the OS temp directory. Check that
    // invariant before recursive cleanup so a platform anomaly cannot widen it.
    const relativeToTemp = path.relative(path.resolve(os.tmpdir()), path.resolve(root))
    assert.ok(relativeToTemp && !relativeToTemp.startsWith(`..${path.sep}`))
    fs.rmSync(root, { recursive: true, force: true })
  })

  const maintained = createSource(root, 'models/source.mjs')
  const maintainedCache = createSource(root, 'core/cache/source.mjs')
  createSource(root, 'models/cache/runtime/external.mjs')

  const discovered = collectFiles(root, new Set(['.mjs']))

  assert.deepEqual(new Set(discovered), new Set([maintained, maintainedCache]))
})
