/** Verify strict Main-owned notification persistence and delivery policy. */

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
  parseUpdatePresenceNotificationRequest,
} from '../dist-electron/presence-notification-contracts.js'
import {
  nextPresenceReminderDelayMs,
  PRESENCE_REMINDER_INTERVAL_MS,
  shouldDeliverCompletionNotification,
  shouldDeliverPresenceReminder,
} from '../dist-electron/presence-notification-policy.js'
import {
  PresenceNotificationPreferencesConflictError,
  PresenceNotificationPreferencesRepository,
  PresenceNotificationPreferencesStorageError,
  PresenceNotificationPreferencesValidationError,
} from '../dist-electron/presence-notification-preferences.js'

async function withTempDirectory(operation) {
  const directory = await mkdtemp(
    path.join(os.tmpdir(), 'elysia-notification-test-'),
  )
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
    completionNotifications: true,
    reminderFrequency: 'daily',
    lastReminderHandledAt: '2026-10-01T13:00:00.000Z',
    ...overrides,
  }
}

test('update contract accepts only exact closed fields', () => {
  assert.deepEqual(parseUpdatePresenceNotificationRequest({
    expectedRevision: 4,
    completionNotifications: true,
    reminderFrequency: 'weekly',
  }), {
    expectedRevision: 4,
    completionNotifications: true,
    reminderFrequency: 'weekly',
  })

  const invalid = [
    null,
    [],
    {},
    { expectedRevision: 0, completionNotifications: false },
    {
      expectedRevision: 0,
      completionNotifications: false,
      reminderFrequency: 'off',
      body: 'renderer controlled',
    },
    {
      expectedRevision: -1,
      completionNotifications: false,
      reminderFrequency: 'off',
    },
    {
      expectedRevision: 0,
      completionNotifications: 'yes',
      reminderFrequency: 'off',
    },
    {
      expectedRevision: 0,
      completionNotifications: false,
      reminderFrequency: 'hourly',
    },
  ]
  for (const value of invalid) {
    assert.throws(() => parseUpdatePresenceNotificationRequest(value))
  }

  const symbolField = {
    expectedRevision: 0,
    completionNotifications: false,
    reminderFrequency: 'off',
  }
  symbolField[Symbol('native-options')] = { urgency: 'critical' }
  assert.throws(() => parseUpdatePresenceNotificationRequest(symbolField))
})

test('missing preferences default fully off without creating a file', async () => {
  await withTempDirectory(async (directory) => {
    const repository = new PresenceNotificationPreferencesRepository(
      path.join(directory, 'presence-notifications.json'),
    )

    const loaded = await repository.load('available')

    assert.deepEqual(loaded, {
      state: {
        revision: 0,
        updatedAt: null,
        completionNotifications: false,
        reminderFrequency: 'off',
        runtime: 'available',
        warning: null,
      },
      lastReminderHandledAt: null,
    })
    assert.deepEqual(await readdir(directory), [])
  })
})

test('valid state excludes the private last reminder timestamp', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'presence-notifications.json')
    await writeFile(filePath, JSON.stringify(canonicalDocument()), 'utf8')
    const repository = new PresenceNotificationPreferencesRepository(filePath)

    const loaded = await repository.load('unsupported')

    assert.deepEqual(loaded.state, {
      revision: 3,
      updatedAt: '2026-10-01T12:00:00.000Z',
      completionNotifications: true,
      reminderFrequency: 'daily',
      runtime: 'unsupported',
      warning: null,
    })
    assert.equal(Object.hasOwn(loaded.state, 'lastReminderHandledAt'), false)
    assert.equal(loaded.lastReminderHandledAt, '2026-10-01T13:00:00.000Z')
  })
})

test('invalid and oversized documents fail closed', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'presence-notifications.json')
    const repository = new PresenceNotificationPreferencesRepository(filePath)
    const invalidDocuments = [
      '{',
      JSON.stringify({}),
      JSON.stringify(canonicalDocument({ schemaVersion: 2 })),
      JSON.stringify({ ...canonicalDocument(), body: 'private prompt' }),
      JSON.stringify(canonicalDocument({ revision: 0 })),
      JSON.stringify(canonicalDocument({ updatedAt: '2026-10-01T12:00:00Z' })),
      JSON.stringify(canonicalDocument({ completionNotifications: 1 })),
      JSON.stringify(canonicalDocument({ reminderFrequency: 'hourly' })),
      JSON.stringify(canonicalDocument({
        reminderFrequency: 'off',
        lastReminderHandledAt: '2026-10-01T13:00:00.000Z',
      })),
      JSON.stringify(canonicalDocument({ lastReminderHandledAt: null })),
      'x'.repeat((16 * 1024) + 1),
    ]

    for (const content of invalidDocuments) {
      await writeFile(filePath, content, 'utf8')
      const loaded = await repository.load('failed')
      assert.equal(loaded.state.completionNotifications, false)
      assert.equal(loaded.state.reminderFrequency, 'off')
      assert.equal(loaded.state.revision, 0)
      assert.equal(loaded.state.runtime, 'failed')
      assert.match(loaded.state.warning, /remain off/u)
      assert.equal(loaded.lastReminderHandledAt, null)
    }
  })
})

test('updates are atomic, revisioned, no-op aware, and re-anchor cadence', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'presence-notifications.json')
    const timestamps = [
      new Date('2026-10-01T14:00:00.000Z'),
      new Date('2026-10-01T15:00:00.000Z'),
      new Date('2026-10-01T16:00:00.000Z'),
    ]
    let clockCalls = 0
    const repository = new PresenceNotificationPreferencesRepository(filePath, {
      now: () => timestamps[clockCalls++] ?? timestamps.at(-1),
    })

    const daily = await repository.update({
      expectedRevision: 0,
      completionNotifications: true,
      reminderFrequency: 'daily',
    })
    assert.equal(daily.state.revision, 1)
    assert.equal(daily.lastReminderHandledAt, '2026-10-01T14:00:00.000Z')

    const noOp = await repository.update({
      expectedRevision: 1,
      completionNotifications: true,
      reminderFrequency: 'daily',
    })
    assert.equal(noOp.state.revision, 1)
    assert.equal(clockCalls, 1)

    const completionOnly = await repository.update({
      expectedRevision: 1,
      completionNotifications: false,
      reminderFrequency: 'daily',
    })
    assert.equal(completionOnly.state.revision, 2)
    assert.equal(
      completionOnly.lastReminderHandledAt,
      '2026-10-01T14:00:00.000Z',
    )

    const weekly = await repository.update({
      expectedRevision: 2,
      completionNotifications: false,
      reminderFrequency: 'weekly',
    })
    assert.equal(weekly.state.revision, 3)
    assert.equal(weekly.lastReminderHandledAt, '2026-10-01T16:00:00.000Z')
    assert.deepEqual(
      JSON.parse(await readFile(filePath, 'utf8')),
      canonicalDocument({
        revision: 3,
        updatedAt: '2026-10-01T16:00:00.000Z',
        completionNotifications: false,
        reminderFrequency: 'weekly',
        lastReminderHandledAt: '2026-10-01T16:00:00.000Z',
      }),
    )

    const off = await repository.update({
      expectedRevision: 3,
      completionNotifications: false,
      reminderFrequency: 'off',
    })
    assert.equal(off.state.revision, 4)
    assert.equal(off.lastReminderHandledAt, null)

    await assert.rejects(repository.update({
      expectedRevision: 2,
      completionNotifications: true,
      reminderFrequency: 'off',
    }), PresenceNotificationPreferencesConflictError)
  })
})

test('same-path concurrent CAS updates permit exactly one winner', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'presence-notifications.json')
    const first = new PresenceNotificationPreferencesRepository(filePath)
    const second = new PresenceNotificationPreferencesRepository(filePath)

    const outcomes = await Promise.allSettled([
      first.update({
        expectedRevision: 0,
        completionNotifications: true,
        reminderFrequency: 'off',
      }),
      second.update({
        expectedRevision: 0,
        completionNotifications: false,
        reminderFrequency: 'daily',
      }),
    ])

    assert.equal(
      outcomes.filter((outcome) => outcome.status === 'fulfilled').length,
      1,
    )
    const rejection = outcomes.find((outcome) => outcome.status === 'rejected')
    assert.ok(rejection)
    assert.ok(
      rejection.reason instanceof PresenceNotificationPreferencesConflictError,
    )
    const loaded = await first.load()
    assert.equal(loaded.state.revision, 1)
    assert.equal(
      loaded.state.completionNotifications
        || loaded.state.reminderFrequency === 'daily',
      true,
    )
  })
})

test('recording a handled cycle stays private and rejects stale timers', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'presence-notifications.json')
    const timestamps = [
      new Date('2026-10-01T12:00:00.000Z'),
      new Date('2026-10-02T12:00:00.000Z'),
    ]
    let clockCalls = 0
    const repository = new PresenceNotificationPreferencesRepository(filePath, {
      now: () => timestamps[clockCalls++] ?? timestamps.at(-1),
    })
    await repository.update({
      expectedRevision: 0,
      completionNotifications: false,
      reminderFrequency: 'daily',
    })

    const originalAnchor = '2026-10-01T12:00:00.000Z'
    const recorded = await repository.recordReminderHandled(
      1,
      'daily',
      originalAnchor,
    )

    assert.equal(recorded.state.revision, 1)
    assert.equal(recorded.state.updatedAt, '2026-10-01T12:00:00.000Z')
    assert.equal(
      recorded.lastReminderHandledAt,
      '2026-10-02T12:00:00.000Z',
    )
    assert.equal(Object.hasOwn(recorded.state, 'lastReminderHandledAt'), false)
    await assert.rejects(
      repository.recordReminderHandled(1, 'daily', originalAnchor),
      PresenceNotificationPreferencesConflictError,
    )
    await assert.rejects(
      repository.recordReminderHandled(0, 'daily', originalAnchor),
      PresenceNotificationPreferencesConflictError,
    )
    await assert.rejects(
      repository.recordReminderHandled(1, 'weekly', originalAnchor),
      PresenceNotificationPreferencesConflictError,
    )
    await assert.rejects(
      repository.recordReminderHandled(1, 'daily', 'not-a-timestamp'),
      PresenceNotificationPreferencesValidationError,
    )
  })
})

test('failed atomic replacement preserves the last known-good file', async () => {
  await withTempDirectory(async (directory) => {
    const filePath = path.join(directory, 'presence-notifications.json')
    const initial = new PresenceNotificationPreferencesRepository(filePath, {
      now: () => new Date('2026-10-01T12:00:00.000Z'),
    })
    await initial.update({
      expectedRevision: 0,
      completionNotifications: true,
      reminderFrequency: 'off',
    })
    const before = await readFile(filePath, 'utf8')
    const failing = new PresenceNotificationPreferencesRepository(filePath, {
      now: () => new Date('2026-10-01T13:00:00.000Z'),
      replaceFile: async () => {
        throw new Error('private native failure')
      },
    })

    await assert.rejects(failing.update({
      expectedRevision: 1,
      completionNotifications: false,
      reminderFrequency: 'daily',
    }), PresenceNotificationPreferencesStorageError)

    assert.equal(await readFile(filePath, 'utf8'), before)
    assert.deepEqual(await readdir(directory), ['presence-notifications.json'])
  })
})

test('repository rejects relative paths and invalid runtime values', async () => {
  assert.throws(
    () => new PresenceNotificationPreferencesRepository('notifications.json'),
    PresenceNotificationPreferencesValidationError,
  )
  await withTempDirectory(async (directory) => {
    const repository = new PresenceNotificationPreferencesRepository(
      path.join(directory, 'presence-notifications.json'),
    )
    await assert.rejects(
      repository.load('starting'),
      PresenceNotificationPreferencesValidationError,
    )
  })
})

test('cadence delays are anchored, bounded, and fail closed', () => {
  const anchor = '2026-10-01T12:00:00.000Z'
  const now = Date.parse('2026-10-01T18:00:00.000Z')
  assert.equal(nextPresenceReminderDelayMs('off', null, null, now), null)
  assert.equal(nextPresenceReminderDelayMs('daily', null, null, now), null)
  assert.equal(nextPresenceReminderDelayMs(
    'daily',
    anchor,
    '2026-09-01T00:00:00.000Z',
    now,
  ), PRESENCE_REMINDER_INTERVAL_MS.daily - (6 * 60 * 60 * 1000))
  assert.equal(nextPresenceReminderDelayMs(
    'weekly',
    anchor,
    null,
    Date.parse('2026-10-20T00:00:00.000Z'),
  ), 0)
  assert.equal(nextPresenceReminderDelayMs(
    'daily',
    anchor,
    null,
    Date.parse('2026-09-30T12:00:00.000Z'),
  ), PRESENCE_REMINDER_INTERVAL_MS.daily)
  assert.equal(nextPresenceReminderDelayMs(
    'weekly',
    '9999-12-31T23:59:59.999Z',
    null,
    now,
  ), PRESENCE_REMINDER_INTERVAL_MS.weekly)
  assert.equal(nextPresenceReminderDelayMs('daily', anchor, null, NaN), null)
})

test('delivery policy requires opt-in, native support, and an unattended app', () => {
  const intent = {
    completionNotifications: true,
    reminderFrequency: 'daily',
    runtime: 'available',
  }
  const background = {
    shutdownStarted: false,
    windowExists: true,
    windowVisible: false,
    windowMinimized: false,
    windowFocused: false,
    backendReady: true,
    backendBusy: false,
  }
  assert.equal(shouldDeliverCompletionNotification(intent, background), true)
  assert.equal(shouldDeliverPresenceReminder(intent, background, true), true)
  assert.equal(shouldDeliverPresenceReminder(
    intent,
    { ...background, backendBusy: true },
    true,
  ), false)
  assert.equal(shouldDeliverCompletionNotification(
    intent,
    { ...background, windowVisible: true, windowFocused: true },
  ), false)
  assert.equal(shouldDeliverCompletionNotification(
    intent,
    { ...background, backendBusy: true },
  ), false)
  assert.equal(shouldDeliverPresenceReminder(
    intent,
    { ...background, windowVisible: true },
    true,
  ), false)
  assert.equal(shouldDeliverPresenceReminder(
    { ...intent, reminderFrequency: 'off' },
    background,
    true,
  ), false)
  assert.equal(shouldDeliverCompletionNotification(
    { ...intent, completionNotifications: false },
    background,
  ), false)
  assert.equal(shouldDeliverCompletionNotification(
    { ...intent, runtime: 'unsupported' },
    background,
  ), false)
  assert.equal(shouldDeliverPresenceReminder(
    intent,
    { ...background, backendReady: false },
    true,
  ), false)
  assert.equal(shouldDeliverPresenceReminder(
    intent,
    { ...background, shutdownStarted: true },
    true,
  ), false)
})
