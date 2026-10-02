/** Verify post-readiness desktop-pet Live2D faults restore its safe fallback. */

import assert from 'node:assert/strict'
import test from 'node:test'

import { bindLive2DRuntimeFailure } from '../src/character/live2d-runtime-failure.ts'

const RUNTIME_ERROR_EVENT = 'elysia:live2d-error'

function readyController() {
  return {
    disposeCount: 0,
    /** Record the exact release required before a static fallback appears. */
    dispose() {
      this.disposeCount += 1
    },
  }
}

test('desktop pet releases its ready controller before image fallback', () => {
  const canvas = new EventTarget()
  const controller = readyController()
  let liveController = controller
  let generation = 4
  let starting = false
  let canvasHidden = false
  let fallbackHidden = true
  const unbind = bindLive2DRuntimeFailure(
    canvas,
    () => {
      const failedController = liveController
      liveController = null
      return failedController
    },
    () => {
      generation += 1
      starting = false
      canvasHidden = true
      fallbackHidden = false
    },
  )

  canvas.dispatchEvent(new Event(RUNTIME_ERROR_EVENT))
  assert.equal(controller.disposeCount, 1)
  assert.equal(liveController, null)
  assert.equal(generation, 5)
  assert.equal(starting, false)
  assert.equal(canvasHidden, true)
  assert.equal(fallbackHidden, false)

  canvas.dispatchEvent(new Event(RUNTIME_ERROR_EVENT))
  assert.equal(controller.disposeCount, 1)
  unbind()
})

test('fallback still runs when a damaged controller throws during disposal', () => {
  const canvas = new EventTarget()
  let fallbackShown = false
  bindLive2DRuntimeFailure(
    canvas,
    () => ({
      /** Model a renderer whose cleanup path is already damaged. */
      dispose() {
        throw new Error('damaged optional renderer')
      },
    }),
    () => {
      fallbackShown = true
    },
  )

  assert.doesNotThrow(() => {
    canvas.dispatchEvent(new Event(RUNTIME_ERROR_EVENT))
  })
  assert.equal(fallbackShown, true)
})
