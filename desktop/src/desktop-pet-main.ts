/** Initialize the isolated Elysia desktop pet with a safe Live2D fallback. */

import { createLive2DController } from './character/live2d-runtime.ts'
import { bindLive2DRuntimeFailure } from './character/live2d-runtime-failure.ts'
import {
  isCharacterPerformancePreference,
  type CharacterPerformanceState,
} from '../electron/character-performance-contracts.ts'

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
const api = window.elysiaDesktopPet

let live2DController: Live2DController | null = null
let live2DGeneration = 0
let live2DStarting = false
let portraitAvailable = !(portrait.complete && portrait.naturalWidth === 0)
let performanceState: CharacterPerformanceState = Object.freeze({
  preference: 'still',
  revision: -1,
})
let unsubscribePerformance = (): void => {}

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
    || performanceState.preference !== 'animated'
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
      framing: 'full-body',
      state: 'idle',
    })
    if (
      generation !== live2DGeneration
      || reducedMotionQuery.matches
      || performanceState.preference !== 'animated'
    ) {
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
  if (
    reducedMotionQuery.matches
    || performanceState.preference !== 'animated'
  ) {
    stopLive2D()
  } else {
    void startLive2D()
  }
}

function acceptPerformanceState(value: unknown): void {
  // Although Main is trusted, the renderer still fails visually closed if an
  // older build or malformed event crosses the isolated-world boundary.
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return
  }
  const candidate = value as Record<string, unknown>
  if (
    !Number.isSafeInteger(candidate.revision)
    || (candidate.revision as number) < 0
    || (candidate.revision as number) < performanceState.revision
    || !isCharacterPerformancePreference(candidate.preference)
  ) {
    return
  }
  performanceState = Object.freeze({
    preference: candidate.preference,
    revision: candidate.revision as number,
  })
  synchronizeMotionPreference()
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
  unsubscribePerformance()
  unbindRuntimeFailure()
  stopLive2D()
}, { once: true })

openMainButton.addEventListener('click', () => {
  if (api === undefined) {
    reportUnavailable()
    return
  }
  void invokePetAction(() => api.openMainChat())
})

hideButton.addEventListener('click', () => {
  if (api === undefined) {
    reportUnavailable()
    return
  }
  void invokePetAction(() => api.hide())
})

if (api === undefined) {
  reportUnavailable()
} else {
  unsubscribePerformance = api.onCharacterPerformanceStateChanged(
    acceptPerformanceState,
  )
  void api.getCharacterPerformanceState()
    .then(acceptPerformanceState)
    .catch(reportUnavailable)
  void api.ready().catch(reportUnavailable)
}
synchronizeMotionPreference()
