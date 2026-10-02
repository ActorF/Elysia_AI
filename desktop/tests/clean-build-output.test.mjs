/**
 * @fileoverview Verify that build cleanup cannot escape its two output folders.
 */

import assert from 'node:assert/strict'
import {
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  stat,
  writeFile,
} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const cleanerSource = path.join(
  desktopRoot,
  'scripts',
  'clean-build-output.mjs',
)

async function pathExists(target) {
  try {
    await stat(target)
    return true
  } catch (error) {
    if (error?.code === 'ENOENT') {
      return false
    }
    throw error
  }
}

async function createIsolatedCleaner() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'elysia-clean-build-'))
  const isolatedDesktop = path.join(root, 'desktop')
  const isolatedScripts = path.join(isolatedDesktop, 'scripts')
  const isolatedCleaner = path.join(isolatedScripts, 'clean-build-output.mjs')
  await mkdir(isolatedScripts, { recursive: true })
  await copyFile(cleanerSource, isolatedCleaner)
  const cleaner = await import(pathToFileURL(isolatedCleaner).href)
  return { cleaner, isolatedDesktop, root }
}

async function seedBuildTree(isolatedDesktop) {
  const rendererOutput = path.join(isolatedDesktop, 'dist')
  const electronOutput = path.join(isolatedDesktop, 'dist-electron')
  const sourceDirectory = path.join(isolatedDesktop, 'src')
  await mkdir(rendererOutput, { recursive: true })
  await mkdir(electronOutput, { recursive: true })
  await mkdir(sourceDirectory, { recursive: true })
  await writeFile(path.join(rendererOutput, 'renderer.js'), 'renderer', 'utf8')
  await writeFile(path.join(electronOutput, 'main.js'), 'electron', 'utf8')
  await writeFile(path.join(sourceDirectory, 'keep.ts'), 'source', 'utf8')
  await writeFile(path.join(isolatedDesktop, 'package.json'), '{}', 'utf8')
  return { electronOutput, rendererOutput, sourceDirectory }
}

test('renderer cleanup deletes only dist', async () => {
  const fixture = await createIsolatedCleaner()
  try {
    const tree = await seedBuildTree(fixture.isolatedDesktop)
    await fixture.cleaner.cleanBuildOutputs('renderer')

    assert.equal(await pathExists(tree.rendererOutput), false)
    assert.equal(await pathExists(tree.electronOutput), true)
    assert.equal(await pathExists(tree.sourceDirectory), true)
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('electron cleanup deletes only dist-electron', async () => {
  const fixture = await createIsolatedCleaner()
  try {
    const tree = await seedBuildTree(fixture.isolatedDesktop)
    await fixture.cleaner.cleanBuildOutputs('electron')

    assert.equal(await pathExists(tree.rendererOutput), true)
    assert.equal(await pathExists(tree.electronOutput), false)
    assert.equal(await pathExists(tree.sourceDirectory), true)
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('all cleanup deletes both outputs and preserves maintained files', async () => {
  const fixture = await createIsolatedCleaner()
  try {
    const tree = await seedBuildTree(fixture.isolatedDesktop)
    await fixture.cleaner.cleanBuildOutputs('all')

    assert.equal(await pathExists(tree.rendererOutput), false)
    assert.equal(await pathExists(tree.electronOutput), false)
    assert.equal(await pathExists(tree.sourceDirectory), true)
    assert.equal(
      await readFile(path.join(fixture.isolatedDesktop, 'package.json'), 'utf8'),
      '{}',
    )
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('cleanup rejects arbitrary paths and resolves direct output children', async () => {
  const fixture = await createIsolatedCleaner()
  try {
    assert.throws(
      () => fixture.cleaner.resolveBuildOutputTargets('../outside'),
      /Unknown build cleanup scope/,
    )
    assert.deepEqual(
      fixture.cleaner.resolveBuildOutputTargets('all'),
      [
        path.join(fixture.isolatedDesktop, 'dist'),
        path.join(fixture.isolatedDesktop, 'dist-electron'),
      ],
    )
  } finally {
    await rm(fixture.root, { recursive: true, force: true })
  }
})

test('package scripts clean once per build phase without changing development', async () => {
  const packageDocument = JSON.parse(
    await readFile(path.join(desktopRoot, 'package.json'), 'utf8'),
  )
  const scripts = packageDocument.scripts

  assert.equal(scripts.dev, 'node scripts/dev.mjs')
  assert.equal(scripts['electron:dev'], 'node scripts/dev.mjs')
  assert.match(scripts['build:renderer'], /^npm run clean:renderer && /)
  assert.match(scripts['electron:build'], /^npm run clean:electron && /)
  assert.equal(
    scripts.build,
    'npm run build:renderer && npm run electron:build',
  )
  assert.match(scripts['test:contract'], /tests\/clean-build-output\.test\.mjs/)
})
