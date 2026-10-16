/** Verify Main-only image export admission, atomic publication, and safe rollback. */

import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, writeFile, readdir, rm, link, symlink, rename, lstat } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { deflateSync } from 'node:zlib'
import test from 'node:test'

import { ImageExportError, ImageExportOwner } from '../dist-electron/image-export.js'

const ARTIFACT = `gallery_${'a'.repeat(32)}`

function crc32(bytes) {
  let checksum = 0xffffffff
  for (const byte of bytes) {
    checksum ^= byte
    for (let bit = 0; bit < 8; bit += 1) {
      checksum = (checksum >>> 1) ^ ((checksum & 1) ? 0xedb88320 : 0)
    }
  }
  return (checksum ^ 0xffffffff) >>> 0
}

function chunk(type, data) {
  const name = Buffer.from(type, 'ascii')
  const encoded = Buffer.alloc(data.length + 12)
  encoded.writeUInt32BE(data.length, 0)
  name.copy(encoded, 4)
  data.copy(encoded, 8)
  encoded.writeUInt32BE(crc32(Buffer.concat([name, data])), encoded.length - 4)
  return encoded
}

function image(overrides = {}) {
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(1, 0)
  ihdr.writeUInt32BE(1, 4)
  ihdr[8] = 8
  ihdr[9] = 2
  const bytes = Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(Buffer.from([0, 255, 0, 0]))),
    chunk('IEND', Buffer.alloc(0)),
  ])
  return { artifactId: ARTIFACT, bytes, mediaType: 'image/png', width: 1, height: 1, ...overrides }
}

async function directory(callback) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'elysia-image-export-'))
  try { await callback(root) }
  finally { await rm(root, { recursive: true, force: true }) }
}

function ownerFor(target, options = {}) {
  return new ImageExportOwner({
    chooseSavePath: async () => target,
    getVerifiedImage: async () => image(),
    ...options,
  })
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((accept, decline) => { resolve = accept; reject = decline })
  return { promise, resolve, reject }
}

test('native dialog precedes artifact-only fetch and publishes the complete PNG', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    const calls = []
    const owner = ownerFor(target, {
      chooseSavePath: async (artifactId, signal) => {
        calls.push(['dialog', artifactId, signal instanceof AbortSignal])
        return target
      },
      getVerifiedImage: async (artifactId, signal) => {
        calls.push(['fetch', artifactId, signal instanceof AbortSignal])
        return image()
      },
    })
    const result = await owner.export(ARTIFACT)

    assert.deepEqual(calls, [['dialog', ARTIFACT, true], ['fetch', ARTIFACT, true]])
    assert.deepEqual(result, { artifactId: ARTIFACT, exported: true })
    assert.equal(Object.isFrozen(result), true)
    assert.deepEqual(await readFile(target), image().bytes)
    assert.deepEqual(await readdir(root), ['portrait.png'])
    assert.equal((await lstat(target)).nlink, 1)
    await owner.close()
  })
})

test('canceling the native dialog fetches no image and creates no file', async () => {
  let fetches = 0
  const owner = ownerFor(null, { getVerifiedImage: async () => { fetches += 1; return image() } })
  assert.deepEqual(await owner.export(ARTIFACT), { artifactId: ARTIFACT, exported: false })
  assert.equal(fetches, 0)
  await owner.close()
})

test('only an opaque artifact ID is admitted before native callbacks', async () => {
  let calls = 0
  const owner = ownerFor(null, { chooseSavePath: async () => { calls += 1; return null } })
  for (const invalid of ['', '../private.png', 'C:\\private.png', `gallery_${'A'.repeat(32)}`, null, {}]) {
    await assert.rejects(owner.export(invalid), (error) => error instanceof ImageExportError && error.reason === 'invalid-artifact')
  }
  assert.equal(calls, 0)
  await owner.close()
})

test('an existing destination is replaced atomically with no backup left over', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    await writeFile(target, 'previous user image')
    const owner = ownerFor(target)
    assert.equal((await owner.export(ARTIFACT)).exported, true)
    assert.deepEqual(await readFile(target), image().bytes)
    assert.deepEqual(await readdir(root), ['portrait.png'])
    assert.equal((await lstat(target)).nlink, 1)
    await owner.close()
  })
})

test('a staging write failure leaves the previous destination unchanged', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    await writeFile(target, 'previous user image')
    const owner = ownerFor(target, { operations: {
      write: async (handle) => { await handle.writeFile('partial'); throw new Error(target) },
    } })
    await assert.rejects(owner.export(ARTIFACT), (error) => !error.message.includes(target) && error.reason === 'failed')
    assert.equal(await readFile(target, 'utf8'), 'previous user image')
    assert.deepEqual(await readdir(root), ['portrait.png'])
    await owner.close()
  })
})

test('a successful short staging write is detected before final publication', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    const owner = ownerFor(target, { operations: { write: async (handle) => handle.writeFile('partial') } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.deepEqual(await readdir(root), [])
    await owner.close()
  })
})

test('staged digest verification catches a same-length corrupt write', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    const owner = ownerFor(target, { operations: {
      write: async (handle, bytes) => handle.writeFile(Buffer.alloc(bytes.length)),
    } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.deepEqual(await readdir(root), [])
    await owner.close()
  })
})

test('flush failure cannot truncate an existing destination', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    await writeFile(target, 'previous')
    const owner = ownerFor(target, { operations: { sync: async () => { throw new Error('private native error') } } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.equal(await readFile(target, 'utf8'), 'previous')
    assert.deepEqual(await readdir(root), ['portrait.png'])
    await owner.close()
  })
})

test('Windows-style rename failure preserves the existing file and removes its backup', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    await writeFile(target, 'previous')
    const owner = ownerFor(target, { operations: { rename: async () => { throw new Error('EPERM') } } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.equal(await readFile(target, 'utf8'), 'previous')
    assert.deepEqual(await readdir(root), ['portrait.png'])
    assert.equal((await lstat(target)).nlink, 1)
    await owner.close()
  })
})

test('an uncertain rename completion rolls the original image back safely', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    await writeFile(target, 'previous')
    let calls = 0
    const owner = ownerFor(target, { operations: { rename: async (source, destination) => {
      calls += 1
      await rename(source, destination)
      if (calls === 1) throw new Error('OS completion became uncertain')
    } } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.equal(calls, 2)
    assert.equal(await readFile(target, 'utf8'), 'previous')
    assert.deepEqual(await readdir(root), ['portrait.png'])
    await owner.close()
  })
})

test('mismatched artifacts and invalid PNG envelopes never alter the selected file', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    await writeFile(target, 'previous')
    for (const invalid of [
      image({ artifactId: `gallery_${'b'.repeat(32)}` }),
      image({ mediaType: 'image/jpeg' }),
      image({ width: 2 }),
      image({ height: 0 }),
      image({ bytes: Buffer.alloc(100) }),
    ]) {
      const owner = ownerFor(target, { getVerifiedImage: async () => invalid })
      await assert.rejects(owner.export(ARTIFACT), ImageExportError)
      assert.equal(await readFile(target, 'utf8'), 'previous')
      await owner.close()
    }
    assert.deepEqual(await readdir(root), ['portrait.png'])
  })
})

test('relative paths and nonregular native targets fail before image fetch', async () => {
  await directory(async (root) => {
    const folder = path.join(root, 'folder')
    await mkdir(folder)
    const invalid = ['relative.png', folder]
    if (process.platform === 'win32') {
      invalid.push(path.join(root, 'file.png:secret'), path.join(root, 'NUL.png'), '\\\\server\\share\\portrait.png')
    }
    let fetches = 0
    for (const target of invalid) {
      const owner = ownerFor(target, { getVerifiedImage: async () => { fetches += 1; return image() } })
      await assert.rejects(owner.export(ARTIFACT), ImageExportError)
      await owner.close()
    }
    assert.equal(fetches, 0)
  })
})

test('existing multiply-linked files are refused without changing either name', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    const alias = path.join(root, 'alias.png')
    await writeFile(target, 'previous')
    await link(target, alias)
    let fetches = 0
    const owner = ownerFor(target, { getVerifiedImage: async () => { fetches += 1; return image() } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.equal(fetches, 0)
    assert.equal(await readFile(alias, 'utf8'), 'previous')
    await owner.close()
  })
})

test('a directory junction or symlink cannot redirect the native export', async () => {
  await directory(async (root) => {
    const actual = path.join(root, 'actual')
    const alias = path.join(root, 'alias')
    await mkdir(actual)
    await symlink(actual, alias, process.platform === 'win32' ? 'junction' : 'dir')
    const owner = ownerFor(path.join(alias, 'portrait.png'))
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.deepEqual(await readdir(actual), [])
    await owner.close()
  })
})

test('a replacement target appearing during Gallery fetch is never overwritten', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    const owner = ownerFor(target, { getVerifiedImage: async () => {
      await writeFile(target, 'new unrelated file')
      return image()
    } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.equal(await readFile(target, 'utf8'), 'new unrelated file')
    assert.deepEqual(await readdir(root), ['portrait.png'])
    await owner.close()
  })
})

test('replacement of the selected parent directory during fetch is detected', async () => {
  await directory(async (root) => {
    const selectedParent = path.join(root, 'selected')
    const previousParent = path.join(root, 'previous')
    await mkdir(selectedParent)
    const owner = ownerFor(path.join(selectedParent, 'portrait.png'), { getVerifiedImage: async () => {
      await rename(selectedParent, previousParent)
      await mkdir(selectedParent)
      return image()
    } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.deepEqual(await readdir(selectedParent), [])
    assert.deepEqual(await readdir(previousParent), [])
    await owner.close()
  })
})

test('a target identity change during staging preserves the replacement file', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    const previous = path.join(root, 'previous.png')
    await writeFile(target, 'previous')
    const owner = ownerFor(target, { operations: { write: async (handle, bytes) => {
      await handle.writeFile(bytes)
      await rename(target, previous)
      await writeFile(target, 'unrelated replacement')
    } } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.equal(await readFile(target, 'utf8'), 'unrelated replacement')
    assert.equal(await readFile(previous, 'utf8'), 'previous')
    assert.deepEqual((await readdir(root)).sort(), ['portrait.png', 'previous.png'])
    await owner.close()
  })
})

test('cleanup refuses to delete an unrelated replacement at its staging pathname', async () => {
  await directory(async (root) => {
    const moved = path.join(root, 'moved-private-staging.png')
    let replacedPath
    const owner = ownerFor(path.join(root, 'portrait.png'), { operations: { write: async (handle, bytes) => {
      await handle.writeFile(bytes)
      const name = (await readdir(root)).find((entry) => entry.endsWith('.tmp'))
      replacedPath = path.join(root, name)
      await rename(replacedPath, moved)
      await writeFile(replacedPath, 'unrelated replacement')
    } } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    assert.equal(await readFile(replacedPath, 'utf8'), 'unrelated replacement')
    assert.deepEqual(await readFile(moved), image().bytes)
    await assert.rejects(owner.close(), ImageExportError)
  })
})

test('uncertain rollback preserves the original backup instead of overwriting an unrelated file', async () => {
  await directory(async (root) => {
    const target = path.join(root, 'portrait.png')
    const generated = path.join(root, 'moved-generated.png')
    await writeFile(target, 'previous')
    const owner = ownerFor(target, { operations: { rename: async (source, destination) => {
      await rename(source, destination)
      await rename(destination, generated)
      await writeFile(destination, 'unrelated replacement')
      throw new Error('Uncertain completion after another writer changed the target')
    } } })
    await assert.rejects(owner.export(ARTIFACT), ImageExportError)
    const backup = (await readdir(root)).find((entry) => entry.endsWith('.bak'))
    assert.ok(backup)
    assert.equal(await readFile(path.join(root, backup), 'utf8'), 'previous')
    assert.equal(await readFile(target, 'utf8'), 'unrelated replacement')
    assert.deepEqual(await readFile(generated), image().bytes)
    await owner.close()
  })
})

test('a concurrent export cannot open a second native dialog', async () => {
  const choice = deferred()
  let dialogs = 0
  const owner = ownerFor(null, { chooseSavePath: async () => { dialogs += 1; return choice.promise } })
  const first = owner.export(ARTIFACT)
  await assert.rejects(owner.export(ARTIFACT), (error) => error.reason === 'busy')
  choice.resolve(null)
  await first
  assert.equal(dialogs, 1)
  await owner.close()
})

test('closing while a native dialog is pending drains without waiting for the user', async () => {
  const choice = deferred()
  const shown = deferred()
  let fetches = 0
  const owner = ownerFor(null, {
    chooseSavePath: async () => { shown.resolve(); return choice.promise },
    getVerifiedImage: async () => { fetches += 1; return image() },
  })
  const operation = owner.export(ARTIFACT)
  await shown.promise
  await owner.close()
  assert.deepEqual(await operation, { artifactId: ARTIFACT, exported: false })
  choice.resolve('C:\\late-private-selection.png')
  await Promise.resolve()
  assert.equal(fetches, 0)
  await assert.rejects(owner.export(ARTIFACT), (error) => error.reason === 'closed')
})

test('closing while the Gallery fetch is pending never writes a late payload', async () => {
  await directory(async (root) => {
    const payload = deferred()
    const fetching = deferred()
    const owner = ownerFor(path.join(root, 'portrait.png'), { getVerifiedImage: async () => {
      fetching.resolve()
      return payload.promise
    } })
    const operation = owner.export(ARTIFACT)
    await fetching.promise
    await owner.close()
    assert.equal((await operation).exported, false)
    payload.resolve(image())
    await Promise.resolve()
    assert.deepEqual(await readdir(root), [])
  })
})

test('closing during staging waits for cleanup and does not publish the image', async () => {
  await directory(async (root) => {
    const written = deferred()
    const resume = deferred()
    const owner = ownerFor(path.join(root, 'portrait.png'), { operations: { write: async (handle, bytes) => {
      await handle.writeFile(bytes)
      written.resolve()
      await resume.promise
    } } })
    const operation = owner.export(ARTIFACT)
    await written.promise
    const closing = owner.close()
    resume.resolve()
    await closing
    assert.equal((await operation).exported, false)
    assert.deepEqual(await readdir(root), [])
  })
})
