/** Exercise image controls through native window sizing and actual wheel scrolling rather than forced DOM scrolling. */

import { expect, type ElectronApplication, type Locator, type Page } from '@playwright/test'

/** Resize the real Electron content area and apply zoom before measuring interactive controls. */
export async function resizeImageWindow(
  application: ElectronApplication,
  page: Page,
  width: number,
  height: number,
  zoom = 1,
): Promise<void> {
  await application.evaluate(({ BrowserWindow }, size) => {
    const window = BrowserWindow.getAllWindows()[0]
    window.setContentSize(size.width, size.height, false)
    window.webContents.setZoomFactor(size.zoom)
  }, { width, height, zoom })
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => { requestAnimationFrame(() => { resolve() }) })
  }))
}

/** Reveal a complete control using wheel input and prove it is inside the visible scrollport. */
export async function revealImageControl(page: Page, scrollport: Locator, control: Locator): Promise<void> {
  await expect(async () => {
    const clip = await scrollport.boundingBox()
    const target = await control.boundingBox()
    if (clip === null || target === null) throw new Error('Image control is not mounted.')
    const viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }))
    const top = Math.max(0, clip.y)
    const bottom = Math.min(viewport.height, clip.y + clip.height)
    if (target.y < top + 4 || target.y + target.height > bottom - 4) {
      await page.mouse.move(Math.min(clip.x + clip.width - 24, viewport.width - 24), (top + bottom) / 2)
      await page.mouse.wheel(0, (target.y < top + 4 ? -1 : 1) * (bottom - top) * 0.6)
      throw new Error('Scrolling the image control into view.')
    }
    expect(target.x).toBeGreaterThanOrEqual(Math.max(0, clip.x) - 1)
    expect(target.x + target.width).toBeLessThanOrEqual(Math.min(viewport.width, clip.x + clip.width) + 1)
  }).toPass({ timeout: 8_000, intervals: [40, 80, 120] })
}
