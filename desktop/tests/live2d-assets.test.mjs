/**
 * @fileoverview Verify generation-scoped routing for one external Live2D model.
 *
 * Fixtures are synthetic descriptors and paths. The tests deliberately never
 * inspect the user's external model collection or copy paid model bytes.
 */

import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'

import {
  LIVE2D_ASSET_DESKTOP_PET_PARTITION,
  LIVE2D_ASSET_HOST,
  LIVE2D_ASSET_MAXIMUM_BYTES,
  LIVE2D_ASSET_PRIVILEGES,
  LIVE2D_ASSET_SCHEME,
  Live2DAssetRegistry,
  materializeLive2DAssetResponse,
} from '../dist-electron/live2d-assets.js'

const GENERATION = '0123456789abcdef0123456789abcdef'
const MODEL_ID = 'model_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const OTHER_MODEL_ID = 'model_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
const MODEL_ROOT = path.resolve('synthetic-live2d-library', 'Elysia Original')
const MANIFEST_PATH = path.join(MODEL_ROOT, 'Elysia.model3.json')
const MOC_PATH = path.join(MODEL_ROOT, 'Elysia.moc3')
const TEXTURE_PATH = path.join(MODEL_ROOT, 'textures', 'texture 00.png')
const PHYSICS_PATH = path.join(MODEL_ROOT, 'physics', 'elysia.physics3.json')

function syntheticResource(kind, absolutePath, sizeBytes) {
  return Object.freeze({
    token: `asset_${kind.padEnd(32, '0').slice(0, 32)}`,
    kind,
    absolutePath,
    relativePath: path.relative(MODEL_ROOT, absolutePath).replaceAll('\\', '/'),
    sizeBytes,
  })
}

function syntheticModel() {
  return Object.freeze({
    id: MODEL_ID,
    displayName: 'Synthetic Elysia',
    manifestPath: MANIFEST_PATH,
    manifestRelativePath: 'Elysia Original/Elysia.model3.json',
    profile: 'standard',
    resources: Object.freeze([
      syntheticResource('manifest', MANIFEST_PATH, 512),
      syntheticResource('moc', MOC_PATH, 4_096),
      syntheticResource('texture', TEXTURE_PATH, 8_192),
      syntheticResource('physics', PHYSICS_PATH, 1_024),
    ]),
  })
}

function expectedUrl(...segments) {
  const encodedPath = segments.map((segment) => encodeURIComponent(segment)).join('/')
  return `${LIVE2D_ASSET_SCHEME}://${LIVE2D_ASSET_HOST}/${encodedPath}`
}

test('declares the isolated protocol capabilities and response ceiling', () => {
  assert.deepEqual(LIVE2D_ASSET_PRIVILEGES, {
    standard: true,
    secure: true,
    supportFetchAPI: true,
    corsEnabled: true,
  })
  assert.equal(LIVE2D_ASSET_DESKTOP_PET_PARTITION, 'elysia-desktop-pet')
  assert.equal(LIVE2D_ASSET_MAXIMUM_BYTES, 64 * 1024 * 1024)
})

test('bootstraps exactly one selected model without exposing native paths', () => {
  const registry = new Live2DAssetRegistry(syntheticModel(), GENERATION)

  assert.deepEqual(registry.bootstrap(), {
    modelId: MODEL_ID,
    manifestUrl: expectedUrl(GENERATION, MODEL_ID, 'Elysia.model3.json'),
  })
  assert.equal(registry.bootstrap()?.manifestUrl.includes(MODEL_ROOT), false)
  assert.equal(Object.isFrozen(registry.bootstrap()), true)

  const emptyRegistry = new Live2DAssetRegistry(null, GENERATION)
  assert.equal(emptyRegistry.bootstrap(), null)
})

test('resolves only exact resources from the current generation and model', () => {
  const registry = new Live2DAssetRegistry(syntheticModel(), GENERATION)
  const cases = [
    ['Elysia.model3.json', MANIFEST_PATH, 512],
    ['Elysia.moc3', MOC_PATH, 4_096],
    ['textures/texture 00.png', TEXTURE_PATH, 8_192],
    ['physics/elysia.physics3.json', PHYSICS_PATH, 1_024],
  ]

  for (const [relativePath, absolutePath, sizeBytes] of cases) {
    const resourceUrl = expectedUrl(
      GENERATION,
      MODEL_ID,
      ...relativePath.split('/'),
    )
    assert.deepEqual(registry.resolve(resourceUrl), {
      absolutePath,
      sizeBytes,
    })
    assert.equal(Object.isFrozen(registry.resolve(resourceUrl)), true)
  }

  assert.equal(
    registry.resolve(expectedUrl(
      'fedcba9876543210fedcba9876543210',
      MODEL_ID,
      'Elysia.moc3',
    )),
    null,
  )
  assert.equal(
    registry.resolve(expectedUrl(GENERATION, OTHER_MODEL_ID, 'Elysia.moc3')),
    null,
  )
})

test('rejects URL aliases, traversal, and resources outside the exact registry', () => {
  const registry = new Live2DAssetRegistry(syntheticModel(), GENERATION)
  const prefix = `${LIVE2D_ASSET_SCHEME}://${LIVE2D_ASSET_HOST}/${GENERATION}/${MODEL_ID}`
  const rejected = [
    `https://${LIVE2D_ASSET_HOST}/${GENERATION}/${MODEL_ID}/Elysia.moc3`,
    `${LIVE2D_ASSET_SCHEME}://other/${GENERATION}/${MODEL_ID}/Elysia.moc3`,
    `${LIVE2D_ASSET_SCHEME}://${LIVE2D_ASSET_HOST}:443/${GENERATION}/${MODEL_ID}/Elysia.moc3`,
    `${LIVE2D_ASSET_SCHEME}://user@${LIVE2D_ASSET_HOST}/${GENERATION}/${MODEL_ID}/Elysia.moc3`,
    `${prefix}/Elysia.moc3?download=1`,
    `${prefix}/Elysia.moc3#fragment`,
    `${prefix}/textures%2Ftexture%2000.png`,
    `${prefix}/textures%5Ctexture%2000.png`,
    `${prefix}/textures/../Elysia.moc3`,
    `${prefix}/textures/%2e%2e/Elysia.moc3`,
    `${prefix}/textures/%252e%252e/Elysia.moc3`,
    `${prefix}/unknown.motion3.json`,
    `${prefix}/textures/other.png`,
    `${prefix}//Elysia.moc3`,
    `${prefix}\\Elysia.moc3`,
    ` ${prefix}/Elysia.moc3`,
  ]

  for (const request of rejected) {
    assert.equal(registry.resolve(request), null, request)
  }
})

test('rejects malformed generations before a registry becomes observable', () => {
  for (const generation of [
    '',
    '0123456789abcde',
    '0123456789ABCDEf',
    'g123456789abcdef',
    '0'.repeat(65),
  ]) {
    assert.throws(
      () => new Live2DAssetRegistry(syntheticModel(), generation),
      /generation is invalid/u,
    )
  }
})

test('asset materialization revokes an in-flight old generation', async () => {
  let releaseSecondChunk
  let generationCurrent = true
  const secondChunkReady = new Promise((resolve) => {
    releaseSecondChunk = resolve
  })
  const response = new Response(new ReadableStream({
    async start(controller) {
      controller.enqueue(new Uint8Array([1, 2]))
      await secondChunkReady
      controller.enqueue(new Uint8Array([3, 4]))
      controller.close()
    },
  }))
  const materialized = materializeLive2DAssetResponse(
    response,
    4,
    () => generationCurrent,
  )
  generationCurrent = false
  releaseSecondChunk()

  assert.equal(await materialized, null)
})

test('asset materialization enforces one exact admitted byte length', async () => {
  const accepted = await materializeLive2DAssetResponse(
    new Response(new Uint8Array([1, 2, 3, 4])),
    4,
    () => true,
  )
  const short = await materializeLive2DAssetResponse(
    new Response(new Uint8Array([1, 2, 3])),
    4,
    () => true,
  )
  const long = await materializeLive2DAssetResponse(
    new Response(new Uint8Array([1, 2, 3, 4, 5])),
    4,
    () => true,
  )

  assert.deepEqual(accepted, new Uint8Array([1, 2, 3, 4]))
  assert.equal(short, null)
  assert.equal(long, null)
})
