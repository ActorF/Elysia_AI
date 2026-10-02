/** Verify bounded discovery of external Live2D desktop-pet model libraries. */

import assert from 'node:assert/strict'
import {
  mkdir,
  mkdtemp,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  DESKTOP_PET_MANIFEST_MAXIMUM_BYTES,
  DesktopPetModelLibraryUnavailableError,
  DesktopPetModelLibraryValidationError,
  findDesktopPetModel,
  presentDesktopPetModelLibrary,
  scanDesktopPetModelLibrary,
} from '../dist-electron/desktop-pet-model-library.js'

const LOOKS = Object.freeze([
  '爱莉希雅人律',
  '爱莉希雅女仆',
  '爱莉希雅泳装',
  '爱莉希雅人律猫猫版',
  '爱莉希雅女仆猫猫版',
  '爱莉希雅泳装猫猫版',
])

async function withTempDirectory(operation) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-model-test-'))
  try {
    return await operation(directory)
  } finally {
    await rm(directory, { force: true, recursive: true })
  }
}

function defaultManifest(overrides = {}) {
  return {
    Version: 3,
    FileReferences: {
      Moc: 'cat.moc3',
      Textures: ['cat.4096/texture_00.png'],
      Physics: 'cat.physics3.json',
      DisplayInfo: 'cat.cdi3.json',
      Expressions: [
        { Name: 'happy', File: 'expression/happy.exp3.json' },
      ],
      ...overrides,
    },
  }
}

async function writeCompleteModel(root, look, profile, options = {}) {
  const modelDirectory = path.join(
    root,
    look,
    look,
    'img',
    profile,
    'cat_model',
  )
  await mkdir(path.join(modelDirectory, 'cat.4096'), { recursive: true })
  await mkdir(path.join(modelDirectory, 'expression'), { recursive: true })
  await writeFile(path.join(modelDirectory, 'cat.moc3'), 'verified-moc', 'utf8')
  await writeFile(
    path.join(modelDirectory, 'cat.4096', 'texture_00.png'),
    'verified-texture',
    'utf8',
  )
  await writeFile(
    path.join(modelDirectory, 'cat.physics3.json'),
    '{"Version":3}',
    'utf8',
  )
  await writeFile(
    path.join(modelDirectory, 'cat.cdi3.json'),
    '{"Version":3}',
    'utf8',
  )
  await writeFile(
    path.join(modelDirectory, 'expression', 'happy.exp3.json'),
    '{"Type":"Live2D Expression"}',
    'utf8',
  )
  await writeFile(
    path.join(modelDirectory, 'cat.model3.json'),
    JSON.stringify(options.manifest ?? defaultManifest()),
    'utf8',
  )
  return modelDirectory
}

test('six looks collapse repeated input profiles and prefer standard', async () => {
  await withTempDirectory(async (root) => {
    for (const look of LOOKS) {
      for (const profile of ['gamepad', 'standard', 'keyboard']) {
        await writeCompleteModel(root, look, profile)
      }
    }
    await writeFile(path.join(root, '安装程序.exe'), 'not executable by scanner', 'utf8')
    await writeFile(path.join(root, 'helper.dll'), 'ignored binary', 'utf8')

    const first = await scanDesktopPetModelLibrary(root)
    const second = await scanDesktopPetModelLibrary(root)

    assert.equal(first.models.length, 6)
    assert.deepEqual(
      first.models.map((model) => model.displayName).sort(),
      [...LOOKS].sort(),
    )
    assert.ok(first.models.every((model) => model.profile === 'standard'))
    assert.ok(first.models.every((model) => /^model_[0-9a-f]{32}$/u.test(model.id)))
    assert.ok(first.models.every((model) => (
      model.resources.every((resource) => !/\.(?:exe|dll)$/iu.test(resource.relativePath))
    )))
    assert.deepEqual(
      first.models.map((model) => model.id),
      second.models.map((model) => model.id),
    )

    const publicState = presentDesktopPetModelLibrary(first)
    assert.equal(publicState.status, 'ready')
    assert.equal(publicState.models.length, 6)
    assert.equal(JSON.stringify(publicState).includes(root), false)
    assert.deepEqual(Object.keys(publicState.models[0]).sort(), [
      'displayName',
      'id',
    ])

    const selected = findDesktopPetModel(first, publicState.models[0].id)
    assert.ok(selected)
    assert.equal(findDesktopPetModel(first, 'model_00000000000000000000000000000000'), null)
  })
})

test('an invalid standard profile falls back to keyboard for that look', async () => {
  await withTempDirectory(async (root) => {
    await writeCompleteModel(root, '爱莉希雅人律', 'standard', {
      manifest: defaultManifest({ Moc: '../outside.moc3' }),
    })
    await writeCompleteModel(root, '爱莉希雅人律', 'keyboard')
    await writeCompleteModel(root, '爱莉希雅人律', 'gamepad')

    const scan = await scanDesktopPetModelLibrary(root)

    assert.equal(scan.models.length, 1)
    assert.equal(scan.models[0].profile, 'keyboard')
    assert.equal(scan.models[0].displayName, '爱莉希雅人律')
  })
})

test('empty folders are valid libraries with no selectable models', async () => {
  await withTempDirectory(async (root) => {
    await writeFile(path.join(root, 'README.txt'), 'No model yet.', 'utf8')
    const scan = await scanDesktopPetModelLibrary(root)

    assert.deepEqual(scan.models, [])
    assert.equal(presentDesktopPetModelLibrary(scan).status, 'empty')
  })
})

test('unsafe references and missing resources reject the entire candidate', async () => {
  const unsafeManifests = [
    defaultManifest({ Moc: '../outside.moc3' }),
    defaultManifest({ Moc: 'C:/Windows/System32/calc.exe' }),
    defaultManifest({ Moc: 'nested\\cat.moc3' }),
    defaultManifest({ Moc: 'missing.moc3' }),
    defaultManifest({ Textures: ['../outside.png'] }),
    defaultManifest({ Textures: ['cat.4096/texture%5f00.png'] }),
    { Version: 2, FileReferences: defaultManifest().FileReferences },
  ]

  for (const manifest of unsafeManifests) {
    await withTempDirectory(async (root) => {
      await writeCompleteModel(root, 'unsafe-look', 'standard', { manifest })
      await assert.rejects(
        scanDesktopPetModelLibrary(root),
        DesktopPetModelLibraryValidationError,
      )
    })
  }
})

test('manifest routes rejected by the protocol registry fail during scanning', async () => {
  await withTempDirectory(async (root) => {
    const modelDirectory = await writeCompleteModel(
      root,
      'unsafe-route',
      'standard',
    )
    await writeFile(
      path.join(modelDirectory, 'bad%name.model3.json'),
      JSON.stringify(defaultManifest()),
      'utf8',
    )
    await rm(path.join(modelDirectory, 'cat.model3.json'))

    await assert.rejects(
      scanDesktopPetModelLibrary(root),
      DesktopPetModelLibraryValidationError,
    )
  })
})

test('oversized manifests and excessive nesting are rejected before loading assets', async () => {
  await withTempDirectory(async (root) => {
    const modelDirectory = path.join(root, 'large', 'large', 'img', 'standard', 'cat_model')
    await mkdir(modelDirectory, { recursive: true })
    await writeFile(
      path.join(modelDirectory, 'cat.model3.json'),
      'x'.repeat(DESKTOP_PET_MANIFEST_MAXIMUM_BYTES + 1),
      'utf8',
    )
    await assert.rejects(
      scanDesktopPetModelLibrary(root),
      DesktopPetModelLibraryValidationError,
    )
  })

  await withTempDirectory(async (root) => {
    let nested = root
    for (let index = 0; index < 18; index += 1) {
      nested = path.join(nested, `level-${index}`)
      await mkdir(nested)
    }
    await assert.rejects(
      scanDesktopPetModelLibrary(root),
      DesktopPetModelLibraryValidationError,
    )
  })
})

test('relative and unavailable roots never become scanner capabilities', async () => {
  await assert.rejects(
    scanDesktopPetModelLibrary('relative/models'),
    DesktopPetModelLibraryValidationError,
  )
  await withTempDirectory(async (root) => {
    await assert.rejects(
      scanDesktopPetModelLibrary(path.join(root, 'missing')),
      DesktopPetModelLibraryUnavailableError,
    )
  })
})

test('nested symbolic links are ignored rather than traversed', {
  skip: process.platform === 'win32' ? 'Windows symlink creation requires privileges.' : false,
}, async () => {
  await withTempDirectory(async (root) => {
    const outside = await mkdtemp(path.join(os.tmpdir(), 'elysia-model-outside-'))
    try {
      await writeCompleteModel(outside, 'outside-look', 'standard')
      await symlink(outside, path.join(root, 'linked-outside'), 'dir')
      const scan = await scanDesktopPetModelLibrary(root)
      assert.deepEqual(scan.models, [])
    } finally {
      await rm(outside, { force: true, recursive: true })
    }
  })
})
