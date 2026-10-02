/**
 * Verify native Desktop Pet folder defaults without opening an operating-system dialog.
 */

import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'

import {
  resolveDesktopPetPickerDefaultPath,
  selectedDesktopPetDirectory,
} from '../dist-electron/desktop-pet-directory-picker.js'

test('prefers an existing saved Desktop Pet directory', async () => {
  const saved = path.resolve('D:/owned/live2d pack')
  const checked = []

  const result = await resolveDesktopPetPickerDefaultPath(
    saved,
    path.resolve('D:/Elysia_AI'),
    false,
    async (candidate) => {
      checked.push(candidate)
      return candidate === saved
    },
  )

  assert.equal(result, saved)
  assert.deepEqual(checked, [saved])
})

test('development picker offers the generic character-data parent', async () => {
  const projectRoot = path.resolve('D:/Elysia_AI')
  const expected = path.join(
    projectRoot,
    'data',
    'characters',
  )

  const result = await resolveDesktopPetPickerDefaultPath(
    null,
    projectRoot,
    false,
    async (candidate) => candidate === expected,
  )

  assert.equal(result, expected)
})

test('packaged picker never probes a checkout-only paid collection', async () => {
  const checked = []

  const result = await resolveDesktopPetPickerDefaultPath(
    null,
    path.resolve('D:/Elysia_AI'),
    true,
    async (candidate) => {
      checked.push(candidate)
      return true
    },
  )

  assert.equal(result, undefined)
  assert.deepEqual(checked, [])
})

test('falls back to the operating-system default when probes fail', async () => {
  const result = await resolveDesktopPetPickerDefaultPath(
    path.resolve('Z:/removed/live2d'),
    path.resolve('D:/Elysia_AI'),
    false,
    async () => {
      throw new Error('drive removed')
    },
  )

  assert.equal(result, undefined)
})

test('accepts exactly one absolute native directory or a cancellation', () => {
  const selected = path.resolve('D:/Elysia_AI/data/characters/付费 模型')

  assert.equal(selectedDesktopPetDirectory({
    canceled: false,
    filePaths: [selected],
  }), selected)
  assert.equal(selectedDesktopPetDirectory({
    canceled: true,
    filePaths: [],
  }), null)
  assert.throws(
    () => selectedDesktopPetDirectory({
      canceled: false,
      filePaths: ['relative/model'],
    }),
    /one absolute directory/u,
  )
  assert.throws(
    () => selectedDesktopPetDirectory({
      canceled: false,
      filePaths: [selected, path.resolve('D:/other')],
    }),
    /one absolute directory/u,
  )
})
