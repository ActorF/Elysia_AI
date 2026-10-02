/** Verify strict Electron-owned desktop-pet persistence and DIP restoration. */

import assert from 'node:assert/strict'
import {
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  parseUpdateDesktopPetRequest,
} from '../dist-electron/desktop-pet-contracts.js'
import {
  DESKTOP_PET_MAX_HEIGHT_DIP,
  DESKTOP_PET_MAX_WIDTH_DIP,
  DesktopPetPreferencesConflictError,
  DesktopPetPreferencesRepository,
  DesktopPetPreferencesStorageError,
  DesktopPetPreferencesValidationError,
  presentDesktopPetLibrary,
  resolveDesktopPetBounds,
} from '../dist-electron/desktop-pet-preferences.js'

const MODEL_ID = 'model_0123456789abcdef0123456789abcdef'
const ALTERNATE_MODEL_ID = 'model_fedcba9876543210fedcba9876543210'
const LIBRARY_PATH = path.resolve(os.tmpdir(), 'elysia-live2d-library')

async function withTempDirectory(operation) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-pet-test-'))
  try {
    return await operation(directory)
  } finally {
    await rm(directory, { force: true, recursive: true })
  }
}

function canonicalDocument(overrides = {}) {
  return {
    schemaVersion: 2,
    revision: 3,
    updatedAt: '2026-10-01T12:00:00.000Z',
    mode: 'hidden',
    placement: {
      displayId: 7,
      x: -640,
      y: 120,
    },
    libraryPath: LIBRARY_PATH,
    selectedModelId: MODEL_ID,
    ...overrides,
  }
}

const DISPLAYS = Object.freeze([
  Object.freeze({
    id: 1,
    primary: true,
    scaleFactor: 1,
    workArea: Object.freeze({ x: 0, y: 0, width: 1920, height: 1080 }),
  }),
  Object.freeze({
    id: 2,
    primary: false,
    scaleFactor: 2,
    workArea: Object.freeze({ x: -1280, y: 0, width: 1280, height: 720 }),
  }),
])

test('update contract accepts only exact closed fields', () => {
  assert.deepEqual(
    parseUpdateDesktopPetRequest({ expectedRevision: 4, mode: 'visible' }),
    { expectedRevision: 4, mode: 'visible' },
  )
  assert.deepEqual(
    parseUpdateDesktopPetRequest({
      expectedRevision: 4,
      mode: 'visible',
      modelId: MODEL_ID,
    }),
    { expectedRevision: 4, mode: 'visible', modelId: MODEL_ID },
  )
  assert.deepEqual(
    parseUpdateDesktopPetRequest({
      expectedRevision: 4,
      mode: 'disabled',
      modelId: null,
    }),
    { expectedRevision: 4, mode: 'disabled', modelId: null },
  )

  const invalid = [
    null,
    [],
    {},
    { expectedRevision: 0 },
    { mode: 'disabled' },
    { expectedRevision: 0, mode: 'visible', x: 10 },
    { expectedRevision: 0, mode: 'visible', modelId: MODEL_ID, x: 10 },
    { expectedRevision: -1, mode: 'visible' },
    { expectedRevision: 0.5, mode: 'visible' },
    { expectedRevision: Number.MAX_SAFE_INTEGER + 1, mode: 'visible' },
    { expectedRevision: 0, mode: 'VISIBLE' },
    { expectedRevision: 0, mode: true },
    { expectedRevision: 0, mode: 'visible', modelId: undefined },
    { expectedRevision: 0, mode: 'visible', modelId: 'cat.model3.json' },
  ]
  for (const value of invalid) {
    assert.throws(() => parseUpdateDesktopPetRequest(value))
  }

  const symbolField = { expectedRevision: 0, mode: 'disabled' }
  symbolField[Symbol('native-path')] = 'forbidden'
  assert.throws(() => parseUpdateDesktopPetRequest(symbolField))
})

test('missing preferences default disabled without creating a file', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const repository = new DesktopPetPreferencesRepository(filePath)
    const loaded = await repository.load()

    assert.deepEqual(loaded, {
      state: {
        revision: 0,
        updatedAt: null,
        mode: 'disabled',
        runtime: 'absent',
        warning: null,
        libraryStatus: 'not-configured',
        folderName: null,
        models: [],
        selectedModelId: null,
      },
      placement: null,
      libraryPath: null,
    })

    const unchanged = await repository.update({
      expectedRevision: 0,
      mode: 'disabled',
    })
    assert.equal(unchanged.state.revision, 0)
    assert.equal(unchanged.state.mode, 'disabled')
    assert.deepEqual(await readdir(directory), [])
  })
})

test('first drag persists disabled schema-v2 state with private placement', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const repository = new DesktopPetPreferencesRepository(filePath)
    await repository.savePlacement({ displayId: 2, x: -700, y: 100 })

    assert.deepEqual(JSON.parse(await readFile(filePath, 'utf8')), {
      schemaVersion: 2,
      revision: 0,
      updatedAt: null,
      mode: 'disabled',
      placement: { displayId: 2, x: -700, y: 100 },
      libraryPath: null,
      selectedModelId: null,
    })
  })
})

test('schema-v1 migration preserves placement but forces the pet off', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    await writeFile(filePath, JSON.stringify({
      schemaVersion: 1,
      revision: 3,
      updatedAt: '2026-10-01T12:00:00.000Z',
      mode: 'visible',
      placement: { displayId: 7, x: -640, y: 120 },
    }), 'utf8')
    const repository = new DesktopPetPreferencesRepository(filePath, {
      now: () => new Date('2026-10-01T12:30:00.000Z'),
    })

    const migrated = await repository.load('visible')
    assert.equal(migrated.state.mode, 'disabled')
    assert.equal(migrated.state.revision, 3)
    assert.equal(migrated.state.libraryStatus, 'not-configured')
    assert.equal(migrated.state.selectedModelId, null)
    assert.match(migrated.state.warning, /previous bundled Desktop Pet/u)
    assert.equal(migrated.libraryPath, null)
    assert.deepEqual(migrated.placement, { displayId: 7, x: -640, y: 120 })

    await repository.update({ expectedRevision: 3, mode: 'hidden' })
    assert.deepEqual(JSON.parse(await readFile(filePath, 'utf8')), {
      schemaVersion: 2,
      revision: 4,
      updatedAt: '2026-10-01T12:30:00.000Z',
      mode: 'hidden',
      placement: { displayId: 7, x: -640, y: 120 },
      libraryPath: null,
      selectedModelId: null,
    })
  })
})

test('valid public state excludes Main-private path and placement', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    await writeFile(filePath, JSON.stringify(canonicalDocument()), 'utf8')
    const repository = new DesktopPetPreferencesRepository(filePath)
    const loaded = await repository.load('visible')

    assert.deepEqual(loaded.state, {
      revision: 3,
      updatedAt: '2026-10-01T12:00:00.000Z',
      mode: 'hidden',
      runtime: 'visible',
      warning: null,
      libraryStatus: 'scanning',
      folderName: path.basename(LIBRARY_PATH),
      models: [],
      selectedModelId: MODEL_ID,
    })
    assert.equal(Object.hasOwn(loaded.state, 'placement'), false)
    assert.equal(Object.hasOwn(loaded.state, 'libraryPath'), false)
    assert.equal(JSON.stringify(loaded.state).includes(LIBRARY_PATH), false)
    assert.equal(loaded.libraryPath, LIBRARY_PATH)
    assert.deepEqual(loaded.placement, { displayId: 7, x: -640, y: 120 })

    const presented = presentDesktopPetLibrary(loaded, {
      status: 'ready',
      folderName: 'Elysia Live2D',
      models: [
        { id: MODEL_ID, displayName: '爱莉希雅人律' },
        { id: ALTERNATE_MODEL_ID, displayName: '爱莉希雅泳装' },
      ],
    })
    assert.equal(presented.state.libraryStatus, 'ready')
    assert.equal(presented.state.models.length, 2)
    assert.equal(JSON.stringify(presented.state).includes(LIBRARY_PATH), false)
    assert.equal(presented.libraryPath, LIBRARY_PATH)
  })
})

test('invalid or oversized documents always fail closed', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const repository = new DesktopPetPreferencesRepository(filePath)
    const invalidDocuments = [
      '{',
      JSON.stringify({}),
      JSON.stringify(canonicalDocument({ schemaVersion: 3 })),
      JSON.stringify({ ...canonicalDocument(), nativePath: 'C:\\secret' }),
      JSON.stringify(canonicalDocument({ revision: -1 })),
      JSON.stringify(canonicalDocument({ updatedAt: '2026-10-01T12:00:00Z' })),
      JSON.stringify(canonicalDocument({ mode: 'animated' })),
      JSON.stringify(canonicalDocument({ libraryPath: 'relative/models' })),
      JSON.stringify(canonicalDocument({ selectedModelId: 'cat.model3.json' })),
      JSON.stringify(canonicalDocument({
        libraryPath: null,
        selectedModelId: MODEL_ID,
      })),
      JSON.stringify(canonicalDocument({
        mode: 'visible',
        libraryPath: LIBRARY_PATH,
        selectedModelId: null,
      })),
      JSON.stringify(canonicalDocument({
        placement: { displayId: 7, x: 0, y: 0, width: 800 },
      })),
      JSON.stringify(canonicalDocument({
        placement: { displayId: 7, x: 1_000_001, y: 0 },
      })),
      'x'.repeat((16 * 1024) + 1),
    ]

    for (const content of invalidDocuments) {
      await writeFile(filePath, content, 'utf8')
      const loaded = await repository.load('failed')
      assert.equal(loaded.state.mode, 'disabled', content.slice(0, 180))
      assert.equal(loaded.state.revision, 0)
      assert.equal(loaded.state.runtime, 'failed')
      assert.equal(loaded.state.libraryStatus, 'not-configured')
      assert.match(loaded.state.warning, /remains off/u)
      assert.equal(loaded.placement, null)
      assert.equal(loaded.libraryPath, null)
    }
  })
})

test('library and mode updates are revisioned, private, and no-op aware', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    let clockCalls = 0
    const repository = new DesktopPetPreferencesRepository(filePath, {
      now: () => {
        clockCalls += 1
        return new Date(`2026-10-01T13:00:0${clockCalls}.000Z`)
      },
    })

    const configured = await repository.updateLibrary({
      expectedRevision: 0,
      libraryPath: LIBRARY_PATH,
      selectedModelId: MODEL_ID,
    }, 'loading')
    assert.equal(configured.state.revision, 1)
    assert.equal(configured.state.mode, 'disabled')
    assert.equal(configured.state.selectedModelId, MODEL_ID)
    assert.equal(configured.state.libraryStatus, 'scanning')
    assert.equal(configured.libraryPath, LIBRARY_PATH)

    const visible = await repository.update({
      expectedRevision: 1,
      mode: 'visible',
    }, 'visible')
    assert.equal(visible.state.revision, 2)
    assert.equal(visible.state.mode, 'visible')
    assert.equal(visible.state.selectedModelId, MODEL_ID)

    const noOp = await repository.update({
      expectedRevision: 2,
      mode: 'visible',
    }, 'visible')
    assert.equal(noOp.state.revision, 2)
    assert.equal(clockCalls, 2)

    const changedModel = await repository.update({
      expectedRevision: 2,
      mode: 'visible',
      modelId: ALTERNATE_MODEL_ID,
    })
    assert.equal(changedModel.state.revision, 3)
    assert.equal(changedModel.state.selectedModelId, ALTERNATE_MODEL_ID)

    assert.deepEqual(
      JSON.parse(await readFile(filePath, 'utf8')),
      canonicalDocument({
        revision: 3,
        updatedAt: '2026-10-01T13:00:03.000Z',
        mode: 'visible',
        placement: null,
        selectedModelId: ALTERNATE_MODEL_ID,
      }),
    )
    assert.deepEqual(await readdir(directory), ['desktop-pet.json'])

    await assert.rejects(
      repository.update({ expectedRevision: 0, mode: 'visible' }),
      DesktopPetPreferencesConflictError,
    )

    const cleared = await repository.updateLibrary({
      expectedRevision: 3,
      libraryPath: null,
      selectedModelId: null,
    })
    assert.equal(cleared.state.revision, 4)
    assert.equal(cleared.state.mode, 'disabled')
    assert.equal(cleared.state.libraryStatus, 'not-configured')
    assert.equal(cleared.state.selectedModelId, null)
    assert.equal(cleared.libraryPath, null)
  })
})

test('library updates reject loose fields, relative paths, and orphan IDs', async () => {
  await withTempDirectory(async (directory) => {
    const repository = new DesktopPetPreferencesRepository(
      path.join(directory, 'desktop-pet.json'),
    )
    await assert.rejects(repository.updateLibrary({
      expectedRevision: 0,
      libraryPath: LIBRARY_PATH,
      selectedModelId: MODEL_ID,
      nativePath: 'forbidden',
    }))
    await assert.rejects(repository.updateLibrary({
      expectedRevision: 0,
      libraryPath: 'relative/models',
      selectedModelId: MODEL_ID,
    }), DesktopPetPreferencesValidationError)
    await assert.rejects(repository.updateLibrary({
      expectedRevision: 0,
      libraryPath: null,
      selectedModelId: MODEL_ID,
    }), DesktopPetPreferencesValidationError)
    await assert.rejects(repository.update({
      expectedRevision: 0,
      mode: 'visible',
    }), DesktopPetPreferencesValidationError)
  })
})

test('same-path concurrent CAS updates permit exactly one winner', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const first = new DesktopPetPreferencesRepository(filePath)
    const second = new DesktopPetPreferencesRepository(filePath)
    await first.updateLibrary({
      expectedRevision: 0,
      libraryPath: LIBRARY_PATH,
      selectedModelId: MODEL_ID,
    })

    const outcomes = await Promise.allSettled([
      first.update({ expectedRevision: 1, mode: 'hidden' }),
      second.update({ expectedRevision: 1, mode: 'visible' }),
    ])

    assert.equal(
      outcomes.filter((outcome) => outcome.status === 'fulfilled').length,
      1,
    )
    const rejection = outcomes.find((outcome) => outcome.status === 'rejected')
    assert.ok(rejection)
    assert.ok(rejection.reason instanceof DesktopPetPreferencesConflictError)
    const loaded = await first.load()
    assert.equal(loaded.state.revision, 2)
    assert.ok(['hidden', 'visible'].includes(loaded.state.mode))
  })
})

test('failed atomic replacement preserves the last known-good file', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const initial = new DesktopPetPreferencesRepository(filePath, {
      now: () => new Date('2026-10-01T14:00:00.000Z'),
    })
    await initial.updateLibrary({
      expectedRevision: 0,
      libraryPath: LIBRARY_PATH,
      selectedModelId: MODEL_ID,
    })
    const before = await readFile(filePath, 'utf8')
    const failing = new DesktopPetPreferencesRepository(filePath, {
      now: () => new Date('2026-10-01T15:00:00.000Z'),
      replaceFile: async () => {
        throw new Error('private native failure')
      },
    })

    await assert.rejects(
      failing.update({ expectedRevision: 1, mode: 'visible' }),
      DesktopPetPreferencesStorageError,
    )

    assert.equal(await readFile(filePath, 'utf8'), before)
    assert.deepEqual(await readdir(directory), ['desktop-pet.json'])
    assert.equal((await initial.load()).state.mode, 'disabled')
  })
})

test('placement writes stay private and do not churn preference revision', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const repository = new DesktopPetPreferencesRepository(filePath, {
      now: () => new Date('2026-10-01T16:00:00.000Z'),
    })
    await repository.update({ expectedRevision: 0, mode: 'hidden' })

    await repository.savePlacement({ displayId: 2, x: -700, y: 100 })
    const loaded = await repository.load('visible')

    assert.equal(loaded.state.revision, 1)
    assert.equal(loaded.state.updatedAt, '2026-10-01T16:00:00.000Z')
    assert.equal(Object.hasOwn(loaded.state, 'placement'), false)
    assert.deepEqual(loaded.placement, { displayId: 2, x: -700, y: 100 })

    await repository.savePlacement({
      displayId: 0xffff_fffe,
      x: -700,
      y: 100,
    })
    assert.deepEqual((await repository.load('visible')).placement, {
      displayId: 0xffff_fffe,
      x: -700,
      y: 100,
    })
    await assert.rejects(
      repository.savePlacement({ displayId: 2, x: 1_000_001, y: 0 }),
      DesktopPetPreferencesValidationError,
    )
  })
})

test('DIP geometry restores and clamps on a scaled negative-coordinate display', () => {
  const bounds = resolveDesktopPetBounds(
    { displayId: 2, x: -2_000, y: 900 },
    DISPLAYS,
    { width: 900, height: 900 },
  )

  assert.deepEqual(bounds, {
    x: -1280,
    y: 160,
    width: DESKTOP_PET_MAX_WIDTH_DIP,
    height: DESKTOP_PET_MAX_HEIGHT_DIP,
  })
})

test('removed displays use the nearest current DIP work area', () => {
  const displays = [
    DISPLAYS[0],
    {
      id: 3,
      primary: false,
      scaleFactor: 1.5,
      workArea: { x: 1920, y: 0, width: 1600, height: 900 },
    },
  ]
  const bounds = resolveDesktopPetBounds(
    { displayId: 99, x: 3_500, y: 850 },
    displays,
    { width: 320, height: 480 },
  )

  assert.deepEqual(bounds, {
    x: 3_200,
    y: 420,
    width: 320,
    height: 480,
  })
})

test('Windows unsigned-hash display IDs remain valid geometry owners', () => {
  const displayId = 0xffff_fffe
  const bounds = resolveDesktopPetBounds(
    { displayId, x: 200, y: 150 },
    [{
      id: displayId,
      primary: true,
      scaleFactor: 1.25,
      workArea: { x: 0, y: 0, width: 1600, height: 900 },
    }],
    { width: 320, height: 480 },
  )

  assert.deepEqual(bounds, {
    x: 200,
    y: 150,
    width: 320,
    height: 480,
  })
})

test('new placement uses the primary work-area edge without scale multiplication', () => {
  const bounds = resolveDesktopPetBounds(
    null,
    DISPLAYS,
    { width: 320, height: 480 },
  )

  assert.deepEqual(bounds, {
    x: 1576,
    y: 576,
    width: 320,
    height: 480,
  })
})

test('invalid geometry cannot create an unbounded native rectangle', () => {
  assert.equal(
    resolveDesktopPetBounds(null, [], { width: 320, height: 480 }),
    null,
  )
  assert.equal(
    resolveDesktopPetBounds(null, DISPLAYS, { width: Number.NaN, height: 480 }),
    null,
  )
  assert.equal(
    resolveDesktopPetBounds(null, [{
      id: 1,
      primary: true,
      scaleFactor: 0,
      workArea: { x: 0, y: 0, width: 100, height: 100 },
    }], { width: 320, height: 480 }),
    null,
  )
})

test('repository rejects relative paths and invalid runtime states', async () => {
  assert.throws(
    () => new DesktopPetPreferencesRepository('desktop-pet.json'),
    DesktopPetPreferencesValidationError,
  )
  await withTempDirectory(async (directory) => {
    const repository = new DesktopPetPreferencesRepository(
      path.join(directory, 'desktop-pet.json'),
    )
    await assert.rejects(
      repository.load('starting'),
      DesktopPetPreferencesValidationError,
    )
  })
})
