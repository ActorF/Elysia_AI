/** Initialize the static, isolated Elysia desktop-pet renderer. */

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
const imageFallback = requireElement<HTMLElement>(
  '#desktop-pet-image-fallback',
)
const status = requireElement<HTMLElement>('#desktop-pet-status')

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
  portrait.hidden = true
  imageFallback.hidden = false
}

if (portrait.complete && portrait.naturalWidth === 0) {
  showImageFallback()
} else {
  portrait.addEventListener('error', showImageFallback, { once: true })
}

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
