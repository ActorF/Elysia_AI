/** Initialize the isolated Elysia desktop pet with a safe Live2D fallback. */

import { createLive2DController } from './character/live2d-runtime.ts'
import { bindLive2DRuntimeFailure } from './character/live2d-runtime-failure.ts'

type Live2DController = Awaited<ReturnType<typeof createLive2DController>>

function requireElement<ElementType extends Element>(
  selector: string,
): ElementType {
  const element = document.querySelector<ElementType>(selector)
  if (element === null) {
    throw new Error(`The desktop-pet document is missing ${selector}.`)
  }
  return element
}

const openMainButton = requireElement<HTMLButtonElement>(
  '#desktop-pet-open-main',
)
const hideButton = requireElement<HTMLButtonElement>(
  '#desktop-pet-hide',
)
const portrait = requireElement<HTMLImageElement>(
  '#desktop-pet-portrait',
)
const live2DCanvas = requireElement<HTMLCanvasElement>(
  '#desktop-pet-live2d',
)
const imageFallback = requireElement<HTMLElement>(
  '#desktop-pet-image-fallback',
)
const status = requireElement<HTMLElement>('#desktop-pet-status')
const reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')

let live2DController: Live2DController | null = null
let live2DGeneration = 0
let live2DStarting = false
let portraitAvailable = !(portrait.complete && portrait.naturalWidth === 0)

function reportUnavailable(): void {
  status.textContent = 'Desktop pet controls are unavailable.'
}

async function invokePetAction(action: () => Promise<void>): Promise<void> {
  openMainButton.disabled = true
  hideButton.disabled = true
  status.textContent = ''
  try {
    await action()
  } catch {
    reportUnavailable()
  } finally {
    openMainButton.disabled = false
    hideButton.disabled = false
  }
}

function showImageFallback(): void {
  // A missing packaged asset must leave an accessible operation entry instead
  // of making the transparent window appear empty or unusable.
  portraitAvailable = false
  if (live2DController === null) {
    portrait.hidden = true
    imageFallback.hidden = false
  }
}

if (portrait.complete && portrait.naturalWidth === 0) {
  showImageFallback()
} else {
  portrait.addEventListener('error', showImageFallback, { once: true })
}

function showStaticPortrait(): void {
  live2DCanvas.hidden = true
  delete live2DCanvas.dataset.live2dReady
  if (portraitAvailable) {
    portrait.hidden = false
    imageFallback.hidden = true
  } else {
    portrait.hidden = true
    imageFallback.hidden = false
  }
}

function stopLive2D(): void {
  // Incrementing the generation also disowns an asynchronous controller that
  // resolves after Reduced Motion or renderer teardown changed the decision.
  live2DGeneration += 1
  live2DStarting = false
  live2DController?.dispose()
  live2DController = null
  showStaticPortrait()
}

async function startLive2D(): Promise<void> {
  if (
    reducedMotionQuery.matches
    || live2DStarting
    || live2DController !== null
  ) {
    return
  }
  live2DStarting = true
  const generation = ++live2DGeneration
  // Keep a laid-out but invisible canvas during initialization. A display:none
  // canvas has no useful client size for the renderer's first viewport.
  live2DCanvas.dataset.live2dReady = 'false'
  live2DCanvas.hidden = false
  try {
    const controller = await createLive2DController(live2DCanvas, {
      emotion: 'neutral',
      state: 'idle',
    })
    if (generation !== live2DGeneration || reducedMotionQuery.matches) {
      controller.dispose()
      return
    }
    live2DController = controller
    portrait.hidden = true
    imageFallback.hidden = true
    live2DCanvas.dataset.live2dReady = 'true'
    live2DCanvas.hidden = false
  } catch {
    // The portrait is intentionally retained for unsupported WebGL, a blocked
    // WASM runtime, or a missing packaged model; pet controls remain usable.
    if (generation === live2DGeneration) {
      showStaticPortrait()
    }
  } finally {
    if (generation === live2DGeneration) {
      live2DStarting = false
    }
  }
}

function synchronizeMotionPreference(): void {
  if (reducedMotionQuery.matches) {
    stopLive2D()
  } else {
    void startLive2D()
  }
}

reducedMotionQuery.addEventListener('change', synchronizeMotionPreference)
const unbindRuntimeFailure = bindLive2DRuntimeFailure(
  live2DCanvas,
  () => {
    const failedController = live2DController
    live2DController = null
    return failedController
  },
  () => {
    // Disown an in-flight creation as well as a ready controller before the
    // portrait returns, so a late resolution cannot replace the fallback.
    live2DGeneration += 1
    live2DStarting = false
    showStaticPortrait()
  },
)
window.addEventListener('beforeunload', () => {
  reducedMotionQuery.removeEventListener('change', synchronizeMotionPreference)
  unbindRuntimeFailure()
  stopLive2D()
}, { once: true })
synchronizeMotionPreference()

openMainButton.addEventListener('click', () => {
  const api = window.elysiaDesktopPet
  if (api === undefined) {
    reportUnavailable()
    return
  }
  void invokePetAction(() => api.openMainChat())
})

hideButton.addEventListener('click', () => {
  const api = window.elysiaDesktopPet
  if (api === undefined) {
    reportUnavailable()
    return
  }
  void invokePetAction(() => api.hide())
})

const api = window.elysiaDesktopPet
if (api === undefined) {
  reportUnavailable()
} else {
  void api.ready().catch(reportUnavailable)
}
