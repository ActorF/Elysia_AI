/**
 * Bind the shared one-shot fallback boundary for post-readiness Live2D faults.
 * Both React presence and the isolated desktop pet use this small contract so
 * a failed animation controller cannot leave an empty or frozen character.
 */

interface DisposableLive2DController {
  dispose(): void
}

const LIVE2D_RUNTIME_ERROR_EVENT = 'elysia:live2d-error'

/**
 * Release one failed controller and run its static-fallback transition.
 *
 * The listener disarms before invoking callbacks because disposal can itself
 * touch WebGL state. Cleanup errors are contained so optional animation can
 * never prevent the caller from restoring its reviewed static surface.
 */
export function bindLive2DRuntimeFailure(
  target: EventTarget,
  releaseController: () => DisposableLive2DController | null,
  showFallback: () => void,
): () => void {
  let active = true
  const handleRuntimeFailure = (): void => {
    if (!active) {
      return
    }
    active = false
    target.removeEventListener(
      LIVE2D_RUNTIME_ERROR_EVENT,
      handleRuntimeFailure,
    )
    try {
      const controller = releaseController()
      try {
        controller?.dispose()
      } catch {
        // A broken renderer may also reject cleanup. The reviewed fallback is
        // still more important than surfacing an optional visual error.
      }
    } finally {
      showFallback()
    }
  }

  target.addEventListener(LIVE2D_RUNTIME_ERROR_EVENT, handleRuntimeFailure)
  return () => {
    if (!active) {
      return
    }
    active = false
    target.removeEventListener(
      LIVE2D_RUNTIME_ERROR_EVENT,
      handleRuntimeFailure,
    )
  }
}
