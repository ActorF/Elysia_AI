/** Exercise explicit conversation migration and the persistent Codex workspace through visible user controls. */

import { expect, type Locator, type Page } from '@playwright/test'

/** Read the retained mode control even while its secondary options popup is closed. */
export function conversationMode(page: Page): Locator {
  return page.getByRole('combobox', { name: 'Conversation mode', exact: true, includeHidden: true })
}

/** Open conversation options and request an explicit mode conversion without assuming it succeeds. */
export async function selectConversationMode(page: Page, mode: 'chat' | 'work'): Promise<void> {
  if (!await conversationMode(page).isVisible()) {
    await page.getByRole('button', { name: 'Conversation options', exact: true }).click()
  }
  await conversationMode(page).selectOption(mode)
}

/** Show or close the persistent workspace while leaving its native owners mounted. */
export async function showWorkspace(page: Page, open = true): Promise<void> {
  const trigger = page.getByRole('button', { name: 'Workspace', exact: true, includeHidden: true })
  const expanded = await trigger.getAttribute('aria-expanded') === 'true'
  if (open && !expanded) await trigger.click()
  if (!open && expanded) await page.getByRole('button', { name: 'Close workspace', exact: true }).click()
  await expect(trigger).toHaveAttribute('aria-expanded', String(open))
}
