/** Initialize the isolated desktop pet with its selected external Live2D model. */

import {
  isDesktopPetModelId,
  type DesktopPetModelBootstrap,
} from '../electron/desktop-pet-contracts.ts'
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

function parseModelBootstrap(value: unknown): DesktopPetModelBootstrap {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Desktop Pet model bootstrap is invalid.')
  }
  const candidate = value as Record<string, unknown>
  if (
    Reflect.ownKeys(candidate).length !== 2
    || !isDesktopPetModelId(candidate.modelId)
    || typeof candidate.manifestUrl !== 'string'
  ) {
    throw new Error('Desktop Pet model bootstrap is invalid.')
  }
  let manifestUrl: URL
  try {
    manifestUrl = new URL(candidate.manifestUrl)
  } catch {
    throw new Error('Desktop Pet model bootstrap is invalid.')
  }
  if (
    manifestUrl.protocol !== 'elysia-pet-asset:'
    || manifestUrl.hostname !== 'model'
    || manifestUrl.search !== ''
    || manifestUrl.hash !== ''
    || manifestUrl.username !== ''
    || manifestUrl.password !== ''
  ) {
    throw new Error('Desktop Pet model bootstrap is invalid.')
  }
  return Object.freeze({
    modelId: candidate.modelId,
    manifestUrl: manifestUrl.toString(),
  })
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
const api = window.elysiaDesktopPet

let live2DController: Live2DController | null = null
let disposed = false
let failureReported = false

function reportUnavailable(message = 'Desktop pet controls are unavailable.'): void {
  status.textContent = message
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

function showFailureFallback(): void {
  live2DCanvas.hidden = true
  delete live2DCanvas.dataset.live2dReady
  portrait.hidden = true
  imageFallback.hidden = false
  reportUnavailable('The selected Live2D model could not be rendered.')
}

function reportRuntimeFailure(): void {
  showFailureFallback()
  if (api === undefined || failureReported || disposed) {
    return
  }
  failureReported = true
  // Main immediately destroys the failed transparent window and publishes a
  // retryable state. The catch intentionally absorbs teardown races because a
  // destroyed WebContents can invalidate its own final IPC settlement.
  void api.failed().catch(() => {})
}

const unbindRuntimeFailure = bindLive2DRuntimeFailure(
  live2DCanvas,
  () => {
    const failedController = live2DController
    live2DController = null
    return failedController
  },
  reportRuntimeFailure,
)

async function startDesktopPet(): Promise<void> {
  if (api === undefined) {
    reportUnavailable()
    return
  }
  live2DCanvas.dataset.live2dReady = 'false'
  live2DCanvas.hidden = false
  const bootstrap = parseModelBootstrap(await api.getModelBootstrap())
  const controller = await createLive2DController(live2DCanvas, {
    emotion: 'neutral',
    framing: 'full-body',
    modelManifestUrl: bootstrap.manifestUrl,
    state: 'idle',
  })
  if (disposed) {
    controller.dispose()
    return
  }
  live2DController = controller
  portrait.hidden = true
  imageFallback.hidden = true
  live2DCanvas.dataset.live2dReady = 'true'
  live2DCanvas.hidden = false
  await api.ready()
}

window.addEventListener('beforeunload', () => {
  disposed = true
  unbindRuntimeFailure()
  live2DController?.dispose()
  live2DController = null
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

void startDesktopPet().catch(() => {
  if (!disposed) {
    reportRuntimeFailure()
  }
})
