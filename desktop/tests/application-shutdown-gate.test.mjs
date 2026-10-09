/** Verify repeated native quit requests cannot bypass mandatory cleanup. */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ApplicationShutdownGate,
} from '../dist-electron/application-shutdown-gate.js'

test('holds repeated quit requests until cleanup explicitly allows exit', () => {
  const gate = new ApplicationShutdownGate()

  assert.equal(gate.request(), 'start')
  assert.equal(gate.request(), 'wait')
  assert.equal(gate.request(), 'wait')

  gate.allowFinalQuit()
  assert.equal(gate.request(), 'allow')
})

test('allows a fresh shutdown attempt after mandatory cleanup fails', () => {
  const gate = new ApplicationShutdownGate()

  assert.equal(gate.request(), 'start')
  gate.retryAfterFailure()
  assert.equal(gate.request(), 'start')
})
