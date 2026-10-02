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
  resolveDesktopPetBounds,
} from '../dist-electron/desktop-pet-preferences.js'

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
    schemaVersion: 1,
    revision: 3,
    updatedAt: '2026-10-01T12:00:00.000Z',
    mode: 'hidden',
    placement: {
      displayId: 7,
      x: -640,
      y: 120,
    },
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

  const invalid = [
    null,
    [],
    {},
    { expectedRevision: 0 },
    { mode: 'disabled' },
    { expectedRevision: 0, mode: 'visible', x: 10 },
    { expectedRevision: -1, mode: 'visible' },
    { expectedRevision: 0.5, mode: 'visible' },
    { expectedRevision: Number.MAX_SAFE_INTEGER + 1, mode: 'visible' },
    { expectedRevision: 0, mode: 'VISIBLE' },
    { expectedRevision: 0, mode: true },
  ]
  for (const value of invalid) {
    assert.throws(() => parseUpdateDesktopPetRequest(value))
  }

  const symbolField = { expectedRevision: 0, mode: 'disabled' }
  symbolField[Symbol('native-path')] = 'forbidden'
  assert.throws(() => parseUpdateDesktopPetRequest(symbolField))
})

test('missing preferences default visible without creating a file', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const repository = new DesktopPetPreferencesRepository(filePath)

    const loaded = await repository.load()

    assert.deepEqual(loaded, {
      state: {
        revision: 0,
        updatedAt: null,
        mode: 'visible',
        runtime: 'absent',
        warning: null,
      },
      placement: null,
    })

    const unchanged = await repository.update({
      expectedRevision: 0,
      mode: 'visible',
    })
    assert.equal(unchanged.state.revision, 0)
    assert.equal(unchanged.state.mode, 'visible')
    assert.deepEqual(await readdir(directory), [])
  })
})

test('first drag persists visible first-run mode with private placement', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const repository = new DesktopPetPreferencesRepository(filePath)

    await repository.savePlacement({ displayId: 2, x: -700, y: 100 })

    assert.deepEqual(JSON.parse(await readFile(filePath, 'utf8')), {
      schemaVersion: 1,
      revision: 0,
      updatedAt: null,
      mode: 'visible',
      placement: { displayId: 2, x: -700, y: 100 },
    })
  })
})

test('valid state excludes Main-private placement from renderer state', async () => {
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
    })
    assert.equal(Object.hasOwn(loaded.state, 'placement'), false)
    assert.deepEqual(loaded.placement, {
      displayId: 7,
      x: -640,
      y: 120,
    })
  })
})

test('invalid or oversized documents always fail closed', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const repository = new DesktopPetPreferencesRepository(filePath)
    const invalidDocuments = [
      '{',
      JSON.stringify({}),
      JSON.stringify(canonicalDocument({ schemaVersion: 2 })),
      JSON.stringify({ ...canonicalDocument(), nativePath: 'C:\\secret' }),
      JSON.stringify(canonicalDocument({ revision: -1 })),
      JSON.stringify(canonicalDocument({ updatedAt: '2026-10-01T12:00:00Z' })),
      JSON.stringify(canonicalDocument({ mode: 'animated' })),
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
      assert.equal(loaded.state.mode, 'disabled')
      assert.equal(loaded.state.revision, 0)
      assert.equal(loaded.state.runtime, 'failed')
      assert.match(loaded.state.warning, /remains off/u)
      assert.equal(loaded.placement, null)
    }
  })
})

test('mode update is atomic, revisioned, canonical, and no-op aware', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    let clockCalls = 0
    const repository = new DesktopPetPreferencesRepository(filePath, {
      now: () => {
        clockCalls += 1
        return new Date('2026-10-01T13:00:00.000Z')
      },
    })

    const hidden = await repository.update({
      expectedRevision: 0,
      mode: 'hidden',
    }, 'loading')
    assert.deepEqual(hidden.state, {
      revision: 1,
      updatedAt: '2026-10-01T13:00:00.000Z',
      mode: 'hidden',
      runtime: 'loading',
      warning: null,
    })
    assert.equal(clockCalls, 1)

    const noOp = await repository.update({
      expectedRevision: 1,
      mode: 'hidden',
    }, 'visible')
    assert.equal(noOp.state.revision, 1)
    assert.equal(noOp.state.updatedAt, hidden.state.updatedAt)
    assert.equal(noOp.state.runtime, 'visible')
    assert.equal(clockCalls, 1)

    const stored = JSON.parse(await readFile(filePath, 'utf8'))
    assert.deepEqual(stored, canonicalDocument({
      revision: 1,
      updatedAt: '2026-10-01T13:00:00.000Z',
      mode: 'hidden',
      placement: null,
    }))
    assert.deepEqual(await readdir(directory), ['desktop-pet.json'])

    await assert.rejects(
      repository.update({ expectedRevision: 0, mode: 'visible' }),
      DesktopPetPreferencesConflictError,
    )
  })
})

test('same-path concurrent CAS updates permit exactly one winner', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const first = new DesktopPetPreferencesRepository(filePath)
    const second = new DesktopPetPreferencesRepository(filePath)

    const outcomes = await Promise.allSettled([
      first.update({ expectedRevision: 0, mode: 'hidden' }),
      second.update({ expectedRevision: 0, mode: 'disabled' }),
    ])

    assert.equal(
      outcomes.filter((outcome) => outcome.status === 'fulfilled').length,
      1,
    )
    const rejection = outcomes.find((outcome) => outcome.status === 'rejected')
    assert.ok(rejection)
    assert.ok(rejection.reason instanceof DesktopPetPreferencesConflictError)
    const loaded = await first.load()
    assert.equal(loaded.state.revision, 1)
    assert.ok(['disabled', 'hidden'].includes(loaded.state.mode))
  })
})

test('failed atomic replacement preserves the last known-good file', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'desktop-pet.json')
    const initial = new DesktopPetPreferencesRepository(filePath, {
      now: () => new Date('2026-10-01T14:00:00.000Z'),
    })
    await initial.update({ expectedRevision: 0, mode: 'hidden' })
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
    assert.equal((await initial.load()).state.mode, 'hidden')
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
