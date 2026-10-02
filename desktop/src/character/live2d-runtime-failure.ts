/**
 * Bind the desktop pet's one-shot recovery boundary after a Live2D fault.
 * The main application character is always static; only the isolated desktop
 * pet owns an animation controller that can require this recovery boundary.
 */

interface DisposableLive2DController {
  dispose(): void
}

const LIVE2D_RUNTIME_ERROR_EVENT = 'elysia:live2d-error'

/**
 * Release one failed controller and run its local failure transition.
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
        // A broken renderer may also reject cleanup. Converging Main to the
        // retryable failed state is more important than surfacing cleanup.
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
