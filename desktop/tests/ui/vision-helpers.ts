/** Share explicit attachment-to-Vision and presentation gestures using embedded synthetic image fixtures only. */

import { expect, type Page } from '@playwright/test'

interface VisionFixture {
  [key: string]: unknown
  fileName: string
  mediaType: string
  sizeBytes: number
}

/** Close only the Vision modal so tests can interact with the underlying conversation. */
export async function closeVision(page: Page): Promise<void> {
  if (await page.getByRole('dialog', { name: 'Vision', exact: true }).isVisible()) {
    await page.getByRole('button', { name: 'Close Vision', exact: true }).click()
  }
}

/** Reopen retained Vision data through the composer screenshot icon without importing another image. */
export async function openVision(page: Page, capture = false): Promise<void> {
  if (!await page.getByRole('dialog', { name: 'Vision', exact: true }).isVisible()) {
    await page.getByRole('button', { name: 'Take screenshot', exact: true }).click()
  }
  const toggle = page.getByRole('dialog', { name: 'Vision', exact: true }).getByRole('button', { name: 'Screenshot', exact: true })
  if ((await toggle.getAttribute('aria-expanded') === 'true') !== capture) await toggle.click()
}

/** Stage a synthetic PNG/JPEG through the existing Add files flow, without starting Vision. */
export async function stageVisionImage(page: Page, fixture: VisionFixture): Promise<void> {
  await closeVision(page)
  await page.evaluate((image) => {
    const control = (window as Window & { elysiaDesktopTest: {
      setSelectedVisionImage(value: unknown): void
      setSelectedFiles(files: unknown[]): void
    } }).elysiaDesktopTest
    control.setSelectedVisionImage(image)
    control.setSelectedFiles([{ name: image.fileName, type: image.mediaType, sizeBytes: image.sizeBytes }])
  }, fixture)
  await page.getByRole('button', { name: 'Choose files', exact: true }).click()
  await expect(page.getByRole('button', { name: `Review image ${fixture.fileName}`, exact: true }).last()).toBeEnabled()
}

/** Import only the attachment explicitly reviewed by the user; optionally leave its native callback pending. */
export async function reviewVisionImage(page: Page, fixture: VisionFixture, waitForImport = true): Promise<void> {
  await stageVisionImage(page, fixture)
  await page.getByRole('button', { name: `Review image ${fixture.fileName}`, exact: true }).last().click()
  if (waitForImport) {
    await expect(page.getByRole('article', { name: `Selected image ${fixture.fileName}`, exact: true }).last()).toBeVisible()
    await expect(page.getByRole('region', { name: 'Vision images', exact: true })).toHaveAttribute('aria-busy', 'false')
  }
}
