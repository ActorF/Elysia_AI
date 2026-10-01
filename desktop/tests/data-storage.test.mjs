/**
 * Verify Main-owned data-root initialization, scanning, movement, and cleanup.
 */

import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rename,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  DATA_STORAGE_CATEGORIES,
  DATA_STORAGE_CLEANABLE_CATEGORIES,
  parseDataStorageCleanupRequest,
} from '../dist-electron/data-storage-contracts.js'
import {
  DataStorageConflictError,
  DataStorageValidationError,
  ElectronDataStorage,
} from '../dist-electron/data-storage.js'

async function withTempDirectory(operation) {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'elysia-storage-test-'))
  try {
    return await operation(directory)
  } finally {
    await rm(directory, { force: true, recursive: true })
  }
}

async function writeDataFile(root, relativePath, content) {
  const filePath = path.join(root, relativePath)
  await mkdir(path.dirname(filePath), { recursive: true })
  await writeFile(filePath, content)
  return filePath
}

async function readJson(filePath) {
  return JSON.parse(await readFile(filePath, 'utf8'))
}

function usageByCategory(inventory) {
  return Object.fromEntries(
    inventory.categories.map((entry) => [entry.category, entry]),
  )
}

test('cleanup parser accepts only unique temporary categories and exact fields', () => {
  const token = '11111111-1111-4111-8111-111111111111'
  assert.deepEqual(parseDataStorageCleanupRequest({
    expectedRevision: 2,
    token,
    categories: ['audio', 'cache', 'logs'],
  }), {
    expectedRevision: 2,
    token,
    categories: ['audio', 'cache', 'logs'],
  })

  const invalid = [
    null,
    {},
    { expectedRevision: 0, token, categories: [] },
    { expectedRevision: -1, token, categories: ['cache'] },
    { expectedRevision: 0, token: '', categories: ['cache'] },
    { expectedRevision: 0, token: 'not-a-uuid', categories: ['cache'] },
    { expectedRevision: 0, token, categories: ['memory'] },
    { expectedRevision: 0, token, categories: ['cache', 'cache'] },
    {
      expectedRevision: 0,
      token,
      categories: ['cache'],
      path: 'renderer-controlled',
    },
  ]
  for (const value of invalid) {
    assert.throws(() => parseDataStorageCleanupRequest(value))
  }
})

test('initialization creates a stable default root without optional directories', async () => {
  await withTempDirectory(async (directory) => {
    const userData = path.join(directory, 'user-data')
    const storage = new ElectronDataStorage({
      userDataDirectory: userData,
      now: () => new Date('2026-10-01T12:00:00.000Z'),
    })

    const initialized = await storage.initialize()

    assert.equal(initialized.state.activeDataRoot, path.join(userData, 'data'))
    assert.equal(initialized.state.revision, 0)
    assert.equal(initialized.state.pendingMove, null)
    assert.deepEqual(initialized.state.retainedRoots, [])
    assert.equal(initialized.legacyWorkspaceCopied, false)
    assert.deepEqual(await readdir(initialized.state.activeDataRoot), ['layout.json'])
    await assert.rejects(
      readdir(path.join(initialized.state.activeDataRoot, 'audio')),
      { code: 'ENOENT' },
    )
    const bootstrap = await readJson(
      path.join(userData, 'data-storage-bootstrap.json'),
    )
    const layout = await readJson(
      path.join(initialized.state.activeDataRoot, 'layout.json'),
    )
    assert.equal(bootstrap.rootId, layout.rootId)
    assert.equal(layout.schemaVersion, 1)

    const inventory = await storage.scan()
    assert.deepEqual(
      inventory.categories.map((entry) => entry.category),
      DATA_STORAGE_CATEGORIES,
    )
    assert.equal(inventory.totalBytes, 0)
    assert.equal(inventory.reclaimableBytes, 0)
    assert.equal(inventory.warning, null)
    assert.equal(inventory.measuredAt, '2026-10-01T12:00:00.000Z')
  })
})

test('first initialization copies only legacy workspace and preserves its source', async () => {
  await withTempDirectory(async (directory) => {
    const legacy = path.join(directory, 'legacy-project')
    const legacyChat = await writeDataFile(
      legacy,
      path.join('workspace', 'chats', 'sessions', 'chat_1.json'),
      '{"message":"hello"}',
    )
    await writeDataFile(
      legacy,
      path.join('models', 'cache', 'runtime.bin'),
      'must-not-migrate',
    )
    const userData = path.join(directory, 'user-data')
    const storage = new ElectronDataStorage({
      userDataDirectory: userData,
      legacyProjectRoot: legacy,
    })

    const initialized = await storage.initialize()

    assert.equal(initialized.legacyWorkspaceCopied, true)
    assert.equal(await readFile(legacyChat, 'utf8'), '{"message":"hello"}')
    assert.equal(
      await readFile(
        path.join(
          initialized.state.activeDataRoot,
          'workspace',
          'chats',
          'sessions',
          'chat_1.json',
        ),
        'utf8',
      ),
      '{"message":"hello"}',
    )
    await assert.rejects(
      readFile(
        path.join(initialized.state.activeDataRoot, 'models', 'cache', 'runtime.bin'),
      ),
      { code: 'ENOENT' },
    )
    assert.equal((await storage.initialize()).legacyWorkspaceCopied, false)
  })
})

test('bounded scan assigns every known tree and does not follow junctions', async (t) => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    })
    const root = (await storage.initialize()).state.activeDataRoot
    await writeDataFile(root, 'workspace/settings/global.json', 'cc')
    await writeDataFile(root, 'workspace/chats/index.json', 'chat')
    await writeDataFile(root, 'workspace/migrations/state.json', 'migration')
    await writeDataFile(root, 'workspace/projects/projects.json', 'project')
    await writeDataFile(root, 'workspace/memory/profile.json', 'memory')
    await writeDataFile(root, 'workspace/recovery/log.json', 'recover')
    await writeDataFile(root, 'workspace/attachments/chat/blob.bin', 'source')
    await writeDataFile(root, 'workspace/knowledge/vectors.sqlite3', 'index')
    await writeDataFile(root, 'audio/reply.wav', 'audio')
    await writeDataFile(root, 'cache/preview.bin', 'cache')
    await writeDataFile(root, 'logs/app.log', 'logs')
    await writeDataFile(root, 'mystery/file.bin', 'other')
    const outside = path.join(directory, 'outside')
    await mkdir(outside)
    await writeDataFile(outside, 'secret.bin', 'not-counted')
    try {
      await symlink(outside, path.join(root, 'cache', 'escape'), 'junction')
    } catch (error) {
      if (error?.code === 'EPERM' || error?.code === 'EACCES') {
        t.skip('This host does not permit a junction test.')
        return
      }
      throw error
    }

    const inventory = await storage.scan()
    const usage = usageByCategory(inventory)

    assert.equal(inventory.blockedEntries, 1)
    assert.equal(inventory.truncated, false)
    assert.match(inventory.warning, /not scanned/u)
    assert.equal(usage.config.bytes, 9)
    assert.equal(usage.chats.bytes, 13)
    assert.equal(usage.projects.bytes, 7)
    assert.equal(usage.memory.bytes, 6)
    assert.equal(usage.sources.bytes, 6)
    assert.equal(usage.indexes.bytes, 5)
    assert.equal(usage.audio.bytes, 5)
    assert.equal(usage.cache.bytes, 5)
    assert.equal(usage.logs.bytes, 4)
    assert.equal(usage.other.bytes, 5)
    assert.equal(inventory.totalBytes, 65)
  })
})

test('scan reports truncation rather than walking beyond configured bounds', async () => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
      limits: { maxEntries: 2 },
    })
    const root = (await storage.initialize()).state.activeDataRoot
    await writeDataFile(root, 'cache/one.bin', '1')
    await writeDataFile(root, 'cache/two.bin', '2')
    await writeDataFile(root, 'cache/three.bin', '3')

    const inventory = await storage.scan()

    assert.equal(inventory.truncated, true)
    assert.match(inventory.warning, /safety limit/u)
    assert.ok(inventory.fileCount < 3)
  })
})

test('move switches the pointer only after verified staging and commit removes safe old root', async () => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
      now: () => new Date('2026-10-01T13:00:00.000Z'),
    })
    const initial = await storage.initialize()
    await writeDataFile(
      initial.state.activeDataRoot,
      'workspace/chats/index.json',
      '{"revision":1}',
    )
    const destination = path.join(directory, 'selected-empty-directory')
    await mkdir(destination)

    const transaction = await storage.prepareMove(destination, 0)

    assert.equal(transaction.previousRoot, initial.state.activeDataRoot)
    assert.equal(transaction.destinationRoot, destination)
    assert.equal(transaction.switchedRevision, 1)
    assert.equal(
      await readFile(path.join(destination, 'workspace/chats/index.json'), 'utf8'),
      '{"revision":1}',
    )
    assert.equal(
      (await storage.initialize()).state.pendingMove.transactionId,
      transaction.transactionId,
    )
    assert.equal(
      await readFile(
        path.join(initial.state.activeDataRoot, 'workspace/chats/index.json'),
        'utf8',
      ),
      '{"revision":1}',
    )

    const committed = await storage.commitMove(transaction)

    assert.equal(committed.state.revision, 2)
    assert.equal(committed.state.pendingMove, null)
    assert.equal(committed.oldRootRetained, false)
    assert.equal(committed.warning, null)
    assert.deepEqual(committed.state.retainedRoots, [])
    await assert.rejects(readdir(initial.state.activeDataRoot), { code: 'ENOENT' })
  })
})

test('commit retains an old root containing unknown content', async () => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    })
    const initial = await storage.initialize()
    const unknown = await writeDataFile(
      initial.state.activeDataRoot,
      'unknown-owner/private.bin',
      'keep-me',
    )
    const destination = path.join(directory, 'new-root')
    await mkdir(destination)
    const transaction = await storage.prepareMove(destination, 0)

    const committed = await storage.commitMove(transaction)

    assert.equal(committed.oldRootRetained, true)
    assert.equal(committed.retainedRoot, initial.state.activeDataRoot)
    assert.match(committed.warning, /retained/u)
    assert.deepEqual(
      committed.state.retainedRoots,
      [initial.state.activeDataRoot],
    )
    assert.equal(await readFile(unknown, 'utf8'), 'keep-me')
    assert.equal(committed.state.activeDataRoot, destination)
    assert.equal(committed.state.pendingMove, null)

    const restartedMain = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    })
    assert.deepEqual(
      (await restartedMain.initialize()).state.retainedRoots,
      [initial.state.activeDataRoot],
    )

    const disconnectedCopy = path.join(directory, 'temporarily-offline-copy')
    await rename(initial.state.activeDataRoot, disconnectedCopy)
    const whileUnavailable = await new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    }).initialize()
    assert.deepEqual(
      whileUnavailable.state.retainedRoots,
      [initial.state.activeDataRoot],
    )
    assert.match(whileUnavailable.warning, /currently unavailable/u)
    await rename(disconnectedCopy, initial.state.activeDataRoot)
    assert.deepEqual(
      (await new ElectronDataStorage({
        userDataDirectory: path.join(directory, 'user-data'),
      }).initialize()).state.retainedRoots,
      [initial.state.activeDataRoot],
    )
  })
})

test('commit retains known data changed after the verified copy', async () => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    })
    const initial = await storage.initialize()
    const chat = await writeDataFile(
      initial.state.activeDataRoot,
      'workspace/chats/index.json',
      '{"revision":1}',
    )
    const destination = path.join(directory, 'new-root')
    await mkdir(destination)
    const transaction = await storage.prepareMove(destination, 0)

    await writeFile(chat, '{"revision":2}')
    const committed = await storage.commitMove(transaction)

    assert.equal(committed.oldRootRetained, true)
    assert.match(committed.warning, /changed/u)
    assert.equal(await readFile(chat, 'utf8'), '{"revision":2}')
    assert.equal(
      await readFile(path.join(destination, 'workspace/chats/index.json'), 'utf8'),
      '{"revision":1}',
    )
  })
})

test('commit finalizes its pointer before an unsafe old root is retained', async () => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    })
    const initial = await storage.initialize()
    const destination = path.join(directory, 'new-root')
    await mkdir(destination)
    const transaction = await storage.prepareMove(destination, 0)
    await writeFile(
      path.join(initial.state.activeDataRoot, 'layout.json'),
      JSON.stringify({
        schemaVersion: 1,
        rootId: '11111111-1111-4111-8111-111111111111',
        createdAt: '2026-10-01T00:00:00.000Z',
      }),
    )

    const committed = await storage.commitMove(transaction)

    assert.equal(committed.oldRootRetained, true)
    assert.match(committed.warning, /retained/u)
    assert.ok(await readdir(initial.state.activeDataRoot))
    const persisted = await storage.initialize()
    assert.equal(persisted.state.activeDataRoot, destination)
    assert.equal(persisted.state.pendingMove, null)
    assert.equal(persisted.state.revision, 2)
  })
})

test('rollback restores the old pointer and rejects altered transactions', async () => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    })
    const initial = await storage.initialize()
    await writeDataFile(initial.state.activeDataRoot, 'workspace/chats/index.json', 'x')
    const destination = path.join(directory, 'new-root')
    await mkdir(destination)
    const transaction = await storage.prepareMove(destination, 0)

    await assert.rejects(
      storage.rollbackMove({ ...transaction, transactionId: randomUUID() }),
      DataStorageConflictError,
    )
    await assert.rejects(
      storage.rollbackMove({ ...transaction, extra: true }),
      DataStorageValidationError,
    )

    const rolledBack = await storage.rollbackMove(transaction)

    assert.equal(rolledBack.state.activeDataRoot, initial.state.activeDataRoot)
    assert.equal(rolledBack.state.revision, 2)
    assert.equal(rolledBack.newRootRetained, false)
    await assert.rejects(readdir(destination), { code: 'ENOENT' })
    assert.equal(
      await readFile(
        path.join(initial.state.activeDataRoot, 'workspace/chats/index.json'),
        'utf8',
      ),
      'x',
    )
  })
})

test('a fresh Main instance can roll back an interrupted pending move', async () => {
  await withTempDirectory(async (directory) => {
    const userDataDirectory = path.join(directory, 'user-data')
    const original = new ElectronDataStorage({ userDataDirectory })
    const initial = await original.initialize()
    await writeDataFile(
      initial.state.activeDataRoot,
      'workspace/chats/index.json',
      'durable',
    )
    const destination = path.join(directory, 'new-root')
    await mkdir(destination)
    await original.prepareMove(destination, 0)

    const restartedMain = new ElectronDataStorage({ userDataDirectory })
    const interrupted = await restartedMain.initialize()
    assert.notEqual(interrupted.state.pendingMove, null)
    const recovered = await restartedMain.rollbackMove(
      interrupted.state.pendingMove,
    )

    assert.equal(recovered.state.activeDataRoot, initial.state.activeDataRoot)
    assert.equal(recovered.state.pendingMove, null)
    assert.equal(
      await readFile(
        path.join(initial.state.activeDataRoot, 'workspace/chats/index.json'),
        'utf8',
      ),
      'durable',
    )
  })
})

test('startup can roll back when a pending destination became unavailable', async () => {
  await withTempDirectory(async (directory) => {
    const userDataDirectory = path.join(directory, 'user-data')
    const original = new ElectronDataStorage({ userDataDirectory })
    const initial = await original.initialize()
    await writeDataFile(
      initial.state.activeDataRoot,
      'workspace/chats/index.json',
      'durable',
    )
    const destination = path.join(directory, 'removable-destination')
    await mkdir(destination)
    await original.prepareMove(destination, 0)
    await rm(destination, { recursive: true })

    const restartedMain = new ElectronDataStorage({ userDataDirectory })
    const interrupted = await restartedMain.initialize()
    assert.notEqual(interrupted.state.pendingMove, null)
    const recovered = await restartedMain.rollbackMove(
      interrupted.state.pendingMove,
    )

    assert.equal(recovered.state.activeDataRoot, initial.state.activeDataRoot)
    assert.equal(recovered.state.pendingMove, null)
    assert.equal(recovered.newRootRetained, false)
    assert.equal(
      await readFile(
        path.join(initial.state.activeDataRoot, 'workspace/chats/index.json'),
        'utf8',
      ),
      'durable',
    )
  })
})

test('move rejects relative, non-empty, overlapping, and linked destinations', async (t) => {
  await withTempDirectory(async (directory) => {
    const applicationRoot = path.join(directory, 'application')
    await mkdir(applicationRoot)
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
      legacyProjectRoot: applicationRoot,
    })
    const initial = await storage.initialize()
    await assert.rejects(
      storage.prepareMove('relative-root', 0),
      DataStorageValidationError,
    )
    const nonEmpty = path.join(directory, 'non-empty')
    await writeDataFile(nonEmpty, 'file.bin', 'x')
    await assert.rejects(
      storage.prepareMove(nonEmpty, 0),
      DataStorageValidationError,
    )
    await assert.rejects(
      storage.prepareMove(path.join(initial.state.activeDataRoot, 'nested'), 0),
      DataStorageValidationError,
    )
    const resourceChild = path.join(applicationRoot, 'private-data')
    await mkdir(resourceChild)
    await assert.rejects(
      storage.prepareMove(resourceChild, 0),
      DataStorageValidationError,
    )

    const realDirectory = path.join(directory, 'real-empty')
    const linkedDirectory = path.join(directory, 'linked-empty')
    await mkdir(realDirectory)
    try {
      await symlink(realDirectory, linkedDirectory, 'junction')
    } catch (error) {
      if (error?.code === 'EPERM' || error?.code === 'EACCES') {
        t.skip('This host does not permit a junction test.')
        return
      }
      throw error
    }
    await assert.rejects(
      storage.prepareMove(linkedDirectory, 0),
      DataStorageValidationError,
    )
  })
})

test('source links stop a move instead of being followed', async (t) => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    })
    const initial = await storage.initialize()
    const outside = path.join(directory, 'outside')
    await mkdir(outside)
    await writeDataFile(outside, 'secret.bin', 'secret')
    await mkdir(path.join(initial.state.activeDataRoot, 'cache'))
    try {
      await symlink(
        outside,
        path.join(initial.state.activeDataRoot, 'cache', 'escape'),
        'junction',
      )
    } catch (error) {
      if (error?.code === 'EPERM' || error?.code === 'EACCES') {
        t.skip('This host does not permit a junction test.')
        return
      }
      throw error
    }
    const destination = path.join(directory, 'destination')
    await mkdir(destination)

    await assert.rejects(
      storage.prepareMove(destination, 0),
      DataStorageValidationError,
    )
    assert.deepEqual(await readdir(destination), [])
    assert.equal(await readFile(path.join(outside, 'secret.bin'), 'utf8'), 'secret')
  })
})

test('cleanup deletes only its closed allowlist and invalidates its token', async () => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    })
    const root = (await storage.initialize()).state.activeDataRoot
    await writeDataFile(root, 'audio/reply.wav', 'audio')
    await writeDataFile(root, 'workspace/cache/preview.bin', 'cache')
    await writeDataFile(root, 'logs/app.log', 'logs')
    const durable = await writeDataFile(root, 'workspace/memory/profile.json', 'memory')
    const modelRuntime = await writeDataFile(
      root,
      'models/cache/GPT-SoVITS/runtime.bin',
      'model',
    )
    const inventory = await storage.scan()

    const cleaned = await storage.cleanup({
      expectedRevision: inventory.revision,
      token: inventory.token,
      categories: DATA_STORAGE_CLEANABLE_CATEGORIES,
    })

    assert.equal(cleaned.reclaimedBytes, 14)
    assert.equal(cleaned.deletedFileCount, 3)
    assert.equal(cleaned.state.revision, 1)
    assert.equal(await readFile(durable, 'utf8'), 'memory')
    assert.equal(await readFile(modelRuntime, 'utf8'), 'model')
    await assert.rejects(readdir(path.join(root, 'audio')), { code: 'ENOENT' })
    await assert.rejects(readdir(path.join(root, 'workspace/cache')), { code: 'ENOENT' })
    await assert.rejects(readdir(path.join(root, 'logs')), { code: 'ENOENT' })
    await assert.rejects(
      storage.cleanup({
        expectedRevision: inventory.revision,
        token: inventory.token,
        categories: ['cache'],
      }),
      DataStorageConflictError,
    )
  })
})

test('cleanup token fails closed when temporary contents change after scan', async () => {
  await withTempDirectory(async (directory) => {
    const storage = new ElectronDataStorage({
      userDataDirectory: path.join(directory, 'user-data'),
    })
    const root = (await storage.initialize()).state.activeDataRoot
    const cacheFile = await writeDataFile(root, 'cache/item.bin', 'one')
    const inventory = await storage.scan()
    await writeFile(cacheFile, 'changed-after-measurement')

    await assert.rejects(
      storage.cleanup({
        expectedRevision: inventory.revision,
        token: inventory.token,
        categories: ['cache'],
      }),
      DataStorageConflictError,
    )
    assert.equal(await readFile(cacheFile, 'utf8'), 'changed-after-measurement')
  })
})

test('invalid bootstrap fails closed without overwriting unmanaged state', async () => {
  await withTempDirectory(async (directory) => {
    const userData = path.join(directory, 'user-data')
    await mkdir(userData)
    const bootstrapPath = path.join(userData, 'data-storage-bootstrap.json')
    await writeFile(bootstrapPath, '{"unexpected":true}')
    const storage = new ElectronDataStorage({ userDataDirectory: userData })

    await assert.rejects(storage.initialize(), DataStorageValidationError)
    assert.equal(await readFile(bootstrapPath, 'utf8'), '{"unexpected":true}')
  })
})
