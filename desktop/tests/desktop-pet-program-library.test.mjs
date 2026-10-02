/** Verify bounded discovery of reviewed external desktop-pet programs. */

import assert from 'node:assert/strict'
import {
  cp,
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
  DESKTOP_PET_PROGRAM_CONFIG_MAXIMUM_BYTES,
  DESKTOP_PET_PROGRAM_LIBRARY_MAX_CANDIDATES,
  DesktopPetProgramLibraryUnavailableError,
  DesktopPetProgramLibraryValidationError,
  findDesktopPetProgram,
  presentDesktopPetProgramLibrary,
  revalidateDesktopPetProgramForLaunch,
  scanDesktopPetProgramLibrary,
} from '../dist-electron/desktop-pet-program-library.js'

const FIXED_CODE_NAMES = Object.freeze([
  'BongoCatMverUI.dll',
  'BongoCatUI.exe',
  'd3dx10_43.dll',
  'MaterialDesignColors.dll',
  'MaterialDesignThemes.Wpf.dll',
  'msvcp140.dll',
  'openal32.dll',
  'sfml-audio-2.dll',
  'sfml-graphics-2.dll',
  'sfml-network-2.dll',
  'sfml-system-2.dll',
  'sfml-window-2.dll',
  'vcruntime140_1.dll',
  'vcruntime140.dll',
])

async function withTempDirectory(operation) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-program-test-'))
  try {
    return await operation(directory)
  } finally {
    await rm(directory, { force: true, recursive: true })
  }
}

function validConfig() {
  return {
    decoration: {},
    standard: {},
    keyboard: {},
    gamepad: {},
    mode: 1,
    network: {},
    workarea: {},
  }
}

function validManifest(overrides = {}) {
  return {
    Version: 3,
    FileReferences: {
      Moc: 'cat.moc3',
      Textures: ['cat.4096/texture_00.png'],
      Physics: 'cat.physics3.json',
      DisplayInfo: 'cat.cdi3.json',
      ...overrides,
    },
  }
}

async function writeModelProfile(programDirectory, profile, manifest = validManifest()) {
  const modelDirectory = path.join(
    programDirectory,
    'img',
    profile,
    'cat_model',
  )
  await mkdir(path.join(modelDirectory, 'cat.4096'), { recursive: true })
  await writeFile(path.join(modelDirectory, 'cat.moc3'), 'moc', 'utf8')
  await writeFile(
    path.join(modelDirectory, 'cat.4096', 'texture_00.png'),
    'texture',
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
    path.join(modelDirectory, 'cat.model3.json'),
    JSON.stringify(manifest),
    'utf8',
  )
}

async function writeStructurallyCompleteCandidate(root, name = '测试角色') {
  const programDirectory = path.join(root, name)
  await mkdir(path.join(programDirectory, 'Resources'), { recursive: true })
  await writeFile(
    path.join(programDirectory, `A${name}.exe`),
    'not the reviewed executable',
    'utf8',
  )
  for (const codeName of FIXED_CODE_NAMES) {
    await writeFile(path.join(programDirectory, codeName), 'untrusted', 'utf8')
  }
  await writeFile(
    path.join(programDirectory, 'config.json'),
    JSON.stringify(validConfig()),
    'utf8',
  )
  await writeFile(path.join(programDirectory, 'Resources', 'cat.ttf'), 'font', 'utf8')
  await writeFile(path.join(programDirectory, 'Resources', 'l2dlogo.png'), 'logo', 'utf8')
  for (const profile of ['standard', 'keyboard', 'gamepad']) {
    await writeModelProfile(programDirectory, profile)
  }
  return programDirectory
}

test('empty folders are valid libraries with no selectable programs', async () => {
  await withTempDirectory(async (root) => {
    await writeFile(path.join(root, 'README.txt'), 'No program yet.', 'utf8')
    const scan = await scanDesktopPetProgramLibrary(root)

    assert.deepEqual(scan.programs, [])
    assert.equal(presentDesktopPetProgramLibrary(scan).status, 'empty')
  })
})

test('launcher-shaped folders fail closed when reviewed hashes do not match', async () => {
  await withTempDirectory(async (root) => {
    await writeStructurallyCompleteCandidate(root)

    await assert.rejects(
      scanDesktopPetProgramLibrary(root),
      DesktopPetProgramLibraryValidationError,
    )
  })
})

test('only the exact A plus directory-name launcher creates a candidate', async () => {
  await withTempDirectory(async (root) => {
    const programDirectory = path.join(root, '测试角色')
    await mkdir(programDirectory)
    await writeFile(
      path.join(programDirectory, 'B测试角色.exe'),
      'wrong launcher name',
      'utf8',
    )

    const scan = await scanDesktopPetProgramLibrary(root)
    assert.deepEqual(scan.programs, [])
  })
})

test('malformed or oversized mutable configuration is rejected before execution', async () => {
  await withTempDirectory(async (root) => {
    const programDirectory = path.join(root, '配置损坏')
    await mkdir(programDirectory)
    await writeFile(path.join(programDirectory, 'A配置损坏.exe'), 'launcher', 'utf8')
    await writeFile(
      path.join(programDirectory, 'config.json'),
      JSON.stringify({ mode: 1 }),
      'utf8',
    )
    await assert.rejects(
      scanDesktopPetProgramLibrary(root),
      DesktopPetProgramLibraryValidationError,
    )
  })

  await withTempDirectory(async (root) => {
    const programDirectory = path.join(root, '配置过大')
    await mkdir(programDirectory)
    await writeFile(path.join(programDirectory, 'A配置过大.exe'), 'launcher', 'utf8')
    await writeFile(
      path.join(programDirectory, 'config.json'),
      'x'.repeat(DESKTOP_PET_PROGRAM_CONFIG_MAXIMUM_BYTES + 1),
      'utf8',
    )
    await assert.rejects(
      scanDesktopPetProgramLibrary(root),
      DesktopPetProgramLibraryValidationError,
    )
  })
})

test('model references cannot leave their profile directory', async () => {
  await withTempDirectory(async (root) => {
    const programDirectory = await writeStructurallyCompleteCandidate(root)
    await writeModelProfile(
      programDirectory,
      'standard',
      validManifest({ Moc: '../../outside.moc3' }),
    )

    await assert.rejects(
      scanDesktopPetProgramLibrary(root),
      DesktopPetProgramLibraryValidationError,
    )
  })
})

test('unreviewed top-level executable code rejects a candidate', async () => {
  await withTempDirectory(async (root) => {
    const programDirectory = await writeStructurallyCompleteCandidate(root)
    await writeFile(path.join(programDirectory, 'injected.dll'), 'code', 'utf8')

    await assert.rejects(
      scanDesktopPetProgramLibrary(root),
      DesktopPetProgramLibraryValidationError,
    )
  })
})

test('candidate counts are bounded before hashes or model data are loaded', async () => {
  await withTempDirectory(async (root) => {
    for (
      let index = 0;
      index <= DESKTOP_PET_PROGRAM_LIBRARY_MAX_CANDIDATES;
      index += 1
    ) {
      const name = `candidate-${index}`
      const programDirectory = path.join(root, name)
      await mkdir(programDirectory)
      await writeFile(path.join(programDirectory, `A${name}.exe`), 'candidate', 'utf8')
    }

    await assert.rejects(
      scanDesktopPetProgramLibrary(root),
      DesktopPetProgramLibraryValidationError,
    )
  })
})

test('renderer presentation and lookup do not disclose native paths', () => {
  const descriptor = Object.freeze({
    id: 'model_0123456789abcdef0123456789abcdef',
    displayName: '测试角色',
    programDirectory: 'C:\\private\\测试角色',
    executablePath: 'C:\\private\\测试角色\\A测试角色.exe',
  })
  const scan = Object.freeze({
    rootPath: 'C:\\private',
    folderName: 'private',
    programs: Object.freeze([descriptor]),
  })

  const presentation = presentDesktopPetProgramLibrary(scan)
  assert.deepEqual(presentation.models, [{
    id: descriptor.id,
    displayName: descriptor.displayName,
  }])
  assert.equal(JSON.stringify(presentation).includes('C:\\private'), false)
  assert.equal(findDesktopPetProgram(scan, descriptor.id), descriptor)
  assert.equal(
    findDesktopPetProgram(scan, 'model_ffffffffffffffffffffffffffffffff'),
    null,
  )
})

test('relative and unavailable roots never become scanner capabilities', async () => {
  await assert.rejects(
    scanDesktopPetProgramLibrary('relative/programs'),
    DesktopPetProgramLibraryValidationError,
  )
  await withTempDirectory(async (root) => {
    await assert.rejects(
      scanDesktopPetProgramLibrary(path.join(root, 'missing')),
      DesktopPetProgramLibraryUnavailableError,
    )
  })
})

test('nested symbolic links are ignored rather than traversed', {
  skip: process.platform === 'win32'
    ? 'Windows symlink creation requires privileges.'
    : false,
}, async () => {
  await withTempDirectory(async (root) => {
    const outside = await mkdtemp(path.join(os.tmpdir(), 'elysia-program-outside-'))
    try {
      await writeStructurallyCompleteCandidate(outside, 'linked-program')
      await symlink(outside, path.join(root, 'linked-outside'), 'dir')
      const scan = await scanDesktopPetProgramLibrary(root)
      assert.deepEqual(scan.programs, [])
    } finally {
      await rm(outside, { force: true, recursive: true })
    }
  })
})

const integrationRoot = process.env.ELYSIA_TEST_DESKTOP_PET_PROGRAM_ROOT

test('the reviewed six-program pack is accepted without exposing paths', {
  skip: integrationRoot
    ? false
    : 'Set ELYSIA_TEST_DESKTOP_PET_PROGRAM_ROOT for the private integration fixture.',
}, async () => {
  const first = await scanDesktopPetProgramLibrary(integrationRoot)
  const second = await scanDesktopPetProgramLibrary(integrationRoot)

  assert.equal(first.programs.length, 6)
  assert.deepEqual(
    first.programs.map((program) => program.id),
    second.programs.map((program) => program.id),
  )
  assert.ok(first.programs.every((program) => /^model_[0-9a-f]{32}$/u.test(program.id)))
  assert.equal(JSON.stringify(presentDesktopPetProgramLibrary(first)).includes(first.rootPath), false)
})

test('launch revalidation rejects a trusted program changed after scanning', {
  skip: integrationRoot
    ? false
    : 'Set ELYSIA_TEST_DESKTOP_PET_PROGRAM_ROOT for the private integration fixture.',
}, async () => {
  const sourceScan = await scanDesktopPetProgramLibrary(integrationRoot)
  assert.ok(sourceScan.programs.length > 0)

  await withTempDirectory(async (root) => {
    const sourceProgram = sourceScan.programs[0]
    const copiedProgram = path.join(root, sourceProgram.displayName)
    await cp(sourceProgram.programDirectory, copiedProgram, {
      errorOnExist: true,
      recursive: true,
    })
    const copiedScan = await scanDesktopPetProgramLibrary(root)
    assert.equal(copiedScan.programs.length, 1)

    const selectedProgram = copiedScan.programs[0]
    await writeFile(
      path.join(selectedProgram.programDirectory, 'BongoCatUI.exe'),
      'replaced after the trusted scan',
      'utf8',
    )

    await assert.rejects(
      revalidateDesktopPetProgramForLaunch(copiedScan, selectedProgram),
      DesktopPetProgramLibraryValidationError,
    )
  })
})
