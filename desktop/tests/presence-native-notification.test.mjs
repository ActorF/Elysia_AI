/** Verify one-slot native notification replacement and stale-event isolation. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ManagedPresenceNativeNotification,
} from '../dist-electron/presence-native-notification.js'

class FakeNativeNotificationHandle {
  constructor(kind, callbacks) {
    this.kind = kind
    this.callbacks = callbacks
    this.showCount = 0
    this.closeCount = 0
    this.detachCount = 0
    this.throwOnShow = false
  }

  show() {
    this.showCount += 1
    if (this.throwOnShow) {
      throw new Error('private native show failure')
    }
  }

  close() {
    this.closeCount += 1
  }

  detach() {
    this.detachCount += 1
  }

  emitClick() {
    this.callbacks.clicked()
  }

  emitClose(reason) {
    this.callbacks.closed(reason)
  }

  emitFailure() {
    this.callbacks.failed()
  }
}

function createHarness() {
  const handles = []
  let openCount = 0
  let failureCount = 0
  const manager = new ManagedPresenceNativeNotification(
    (kind, callbacks) => {
      const handle = new FakeNativeNotificationHandle(kind, callbacks)
      handles.push(handle)
      return handle
    },
    () => { openCount += 1 },
    () => { failureCount += 1 },
  )
  return {
    manager,
    handles,
    openCount: () => openCount,
    failureCount: () => failureCount,
  }
}

test('completion owns the active global slot ahead of reminders', () => {
  const harness = createHarness()

  assert.equal(harness.manager.show('completion'), true)
  assert.equal(harness.manager.hasActiveNotification(), true)
  assert.equal(harness.manager.show('reminder'), false)
  assert.equal(harness.handles.length, 1)
  assert.equal(harness.handles[0].closeCount, 0)

  assert.equal(harness.manager.show('completion'), true)
  assert.equal(harness.handles.length, 2)
  assert.equal(harness.handles[0].detachCount, 1)
  assert.equal(harness.handles[0].closeCount, 1)
  assert.equal(harness.handles[1].showCount, 1)
})

test('timed-out Windows handle remains removable but no longer blocks', () => {
  const harness = createHarness()
  assert.equal(harness.manager.show('reminder'), true)
  const timedOut = harness.handles[0]

  timedOut.emitClose('timedOut')

  assert.equal(harness.manager.hasActiveNotification(), false)
  assert.equal(timedOut.detachCount, 0)
  assert.equal(timedOut.closeCount, 0)
  assert.equal(harness.manager.show('reminder'), true)
  assert.equal(timedOut.detachCount, 1)
  assert.equal(timedOut.closeCount, 1)
})

test('retired late callbacks cannot open the app or fail a replacement', () => {
  const harness = createHarness()
  harness.manager.show('reminder')
  const retired = harness.handles[0]
  harness.manager.show('completion')
  const current = harness.handles[1]

  retired.emitClick()
  retired.emitFailure()

  assert.equal(harness.openCount(), 0)
  assert.equal(harness.failureCount(), 0)
  assert.equal(harness.manager.hasActiveNotification(), true)

  current.emitClick()
  assert.equal(harness.openCount(), 1)
  assert.equal(harness.manager.hasActiveNotification(), false)
  current.emitFailure()
  assert.equal(harness.failureCount(), 0)
})

test('explicit close removes a timed-out notification center entry', () => {
  const harness = createHarness()
  harness.manager.show('reminder')
  const handle = harness.handles[0]
  handle.emitClose('timedOut')

  harness.manager.close('reminder')

  assert.equal(handle.detachCount, 1)
  assert.equal(handle.closeCount, 1)
  handle.emitClick()
  handle.emitFailure()
  assert.equal(harness.openCount(), 0)
  assert.equal(harness.failureCount(), 0)
})

test('native show failure retires the handle and reports once', () => {
  const handles = []
  let failures = 0
  const manager = new ManagedPresenceNativeNotification(
    (kind, callbacks) => {
      const handle = new FakeNativeNotificationHandle(kind, callbacks)
      handle.throwOnShow = true
      handles.push(handle)
      return handle
    },
    () => {},
    () => { failures += 1 },
  )

  assert.equal(manager.show('completion'), false)
  assert.equal(manager.hasActiveNotification(), false)
  assert.equal(handles[0].detachCount, 1)
  assert.equal(handles[0].closeCount, 1)
  assert.equal(failures, 1)
  handles[0].emitFailure()
  assert.equal(failures, 1)
})

test('user cancellation releases a slot without redundant native close', () => {
  const harness = createHarness()
  harness.manager.show('reminder')
  const cancelled = harness.handles[0]

  cancelled.emitClose('userCanceled')

  assert.equal(harness.manager.hasActiveNotification(), false)
  assert.equal(cancelled.detachCount, 1)
  assert.equal(cancelled.closeCount, 0)
  assert.equal(harness.manager.show('reminder'), true)
})
