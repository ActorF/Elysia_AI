/** Exercise advanced manual research through Settings while preserving native task and draft navigation boundaries. */

import { expect, type Page } from '@playwright/test'

/** Open the Internet category and retained research disclosure without issuing a provider request. */
export async function openInternetResearch(page: Page): Promise<void> {
  if (!await page.getByRole('heading', { name: 'Settings', exact: true }).isVisible()) {
    await page.getByRole('button', { name: 'Settings', exact: true }).click()
  }
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()
  await page.getByRole('navigation', { name: 'Settings categories', exact: true })
    .getByRole('button', { name: 'Internet', exact: true }).click()
  const disclosure = page.locator('.internet-settings-advanced')
  if (!await disclosure.evaluate((element) => (element as HTMLDetailsElement).open)) {
    await disclosure.getByText('Advanced research tools', { exact: true }).click()
  }
  const toggle = page.getByRole('region', { name: 'Internet research', exact: true }).getByRole('button', { name: 'Internet', exact: true })
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click()
}

/** Leave completed research and expose the actual Chat composer for draft assertions. */
export async function returnToChat(page: Page): Promise<void> {
  if (await page.getByRole('heading', { name: 'Settings', exact: true }).isVisible()) {
    const research = page.getByRole('region', { name: 'Internet research', exact: true })
    if (await research.count() !== 0) await expect(research).toHaveAttribute('aria-busy', 'false')
    await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  }
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeHidden()
  await expect(page.getByLabel('Message Elysia', { exact: true })).toBeVisible()
}

/** Prove navigation cannot unmount a research owner while its physical native callback is pending. */
export async function expectResearchNavigationLocked(page: Page): Promise<void> {
  await expect(page.getByRole('region', { name: 'Internet research', exact: true })).toHaveAttribute('aria-busy', 'true')
  await page.getByRole('button', { name: 'Back to chat', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()
  await expect(page.getByLabel('Message Elysia', { exact: true })).toBeHidden()
}
