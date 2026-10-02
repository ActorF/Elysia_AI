/**
 * @fileoverview Verify the packaged Live2D protocol's closed file allowlist.
 */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'

import {
  LIVE2D_ASSET_HOST,
  LIVE2D_ASSET_DESKTOP_PET_PARTITION,
  LIVE2D_ASSET_MAXIMUM_BYTES,
  LIVE2D_ASSET_PRIVILEGES,
  LIVE2D_ASSET_SCHEME,
  resolveLive2DAssetPath,
} from '../dist-electron/live2d-assets.js'

const applicationPath = path.resolve('fixture-application')
const assetRoot = path.join(
  applicationPath,
  'dist',
  'character',
  'live2d',
  'elysia',
)

test('scheme privileges support secure fetches without bypassing CSP', () => {
  assert.deepEqual(LIVE2D_ASSET_PRIVILEGES, {
    standard: true,
    secure: true,
    supportFetchAPI: true,
    corsEnabled: true,
  })
  assert.equal(LIVE2D_ASSET_DESKTOP_PET_PARTITION, 'elysia-desktop-pet')
  assert.equal(LIVE2D_ASSET_MAXIMUM_BYTES, 8 * 1024 * 1024)
})

test('renderer policies expose fetch and WASM without exposing the asset scheme as images', async () => {
  for (const entry of ['index.html', 'pet.html']) {
    const html = await readFile(new URL(`../${entry}`, import.meta.url), 'utf8')
    const policy = /http-equiv="Content-Security-Policy"\s+content="([^"]+)"/u.exec(html)?.[1]
    assert.ok(policy, `${entry} must declare its CSP in the document prologue`)
    assert.match(policy, /script-src 'self' 'wasm-unsafe-eval'/u)
    assert.doesNotMatch(policy, /(?:^|\s)'unsafe-eval'(?:\s|;|$)/u)
    assert.match(policy, /connect-src [^;]*elysia-asset:/u)
    assert.match(policy, /img-src [^;]*blob:/u)
    assert.doesNotMatch(policy, /img-src [^;]*elysia-asset:/u)
  }
})

test('only the three packaged Live2D files resolve', () => {
  const prefix = `${LIVE2D_ASSET_SCHEME}://${LIVE2D_ASSET_HOST}`
  assert.equal(
    resolveLive2DAssetPath(
      `${prefix}/live2d/elysia/model.model3.json`,
      applicationPath,
    ),
    path.join(assetRoot, 'model.model3.json'),
  )
  assert.equal(
    resolveLive2DAssetPath(
      `${prefix}/live2d/elysia/model.moc3`,
      applicationPath,
    ),
    path.join(assetRoot, 'model.moc3'),
  )
  assert.equal(
    resolveLive2DAssetPath(
      `${prefix}/live2d/elysia/textures/atlas.png`,
      applicationPath,
    ),
    path.join(assetRoot, 'textures', 'atlas.png'),
  )
})

test('non-canonical protocol requests cannot reach the application tree', () => {
  const prefix = `${LIVE2D_ASSET_SCHEME}://${LIVE2D_ASSET_HOST}`
  const rejected = [
    'https://character/live2d/elysia/model.moc3',
    `${LIVE2D_ASSET_SCHEME}://other/live2d/elysia/model.moc3`,
    `${LIVE2D_ASSET_SCHEME}://character:443/live2d/elysia/model.moc3`,
    `${LIVE2D_ASSET_SCHEME}://user@character/live2d/elysia/model.moc3`,
    `${prefix}/live2d/elysia/model.moc3?download=1`,
    `${prefix}/live2d/elysia/model.moc3#fragment`,
    `${prefix}/live2d/elysia/model.physics3.json`,
    `${prefix}/live2d/elysia/textures/other.png`,
    `${prefix}/live2d/elysia/../elysia/model.moc3`,
    `${prefix}/live2d/elysia/%2e%2e/elysia/model.moc3`,
    `${prefix}/live2d/elysia%2fmodel.moc3`,
    `${prefix}/live2d/elysia%5cmodel.moc3`,
    `${prefix}/live2d//elysia/model.moc3`,
    `${prefix}\\live2d\\elysia\\model.moc3`,
    ` ${prefix}/live2d/elysia/model.moc3`,
  ]

  for (const request of rejected) {
    assert.equal(
      resolveLive2DAssetPath(request, applicationPath),
      null,
      request,
    )
  }
})
