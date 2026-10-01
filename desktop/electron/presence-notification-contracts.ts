/**
 * Define the closed Electron-only contract for optional system notifications.
 *
 * Electron Main owns delivery and persistence. The renderer can only select
 * reviewed booleans and frequencies; it cannot provide notification text,
 * native options, schedules, links, or arbitrary IPC channel names.
 */

/** User-selected cadence for neutral reminders while Elysia is already open. */
export type PresenceReminderFrequency = 'off' | 'daily' | 'weekly'

/** Current native-notification outcome, separate from persisted user intent. */
export type PresenceNotificationRuntime =
  | 'available'
  | 'unsupported'
  | 'failed'

/** Renderer-safe notification preferences and native availability snapshot. */
export interface PresenceNotificationState {
  readonly revision: number
  readonly updatedAt: string | null
  readonly completionNotifications: boolean
  readonly reminderFrequency: PresenceReminderFrequency
  readonly runtime: PresenceNotificationRuntime
  readonly warning: string | null
}

/** Replace both optional notification choices using optimistic concurrency. */
export interface UpdatePresenceNotificationRequest {
  readonly expectedRevision: number
  readonly completionNotifications: boolean
  readonly reminderFrequency: PresenceReminderFrequency
}

const PRESENCE_REMINDER_FREQUENCIES: ReadonlySet<string> = new Set([
  'off',
  'daily',
  'weekly',
])

/** Narrow an unknown value to the complete supported reminder-frequency set. */
export function isPresenceReminderFrequency(
  value: unknown,
): value is PresenceReminderFrequency {
  return typeof value === 'string'
    && PRESENCE_REMINDER_FREQUENCIES.has(value)
}

/**
 * Revalidate one renderer-originated update before persistence.
 *
 * Exact fields prevent a future or compromised renderer from supplying native
 * notification text, URLs, urgency, sounds, actions, or arbitrary schedules.
 */
export function parseUpdatePresenceNotificationRequest(
  value: unknown,
): UpdatePresenceNotificationRequest {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Presence notification update is invalid.')
  }
  const fields = Reflect.ownKeys(value)
  if (
    fields.length !== 3
    || !fields.includes('expectedRevision')
    || !fields.includes('completionNotifications')
    || !fields.includes('reminderFrequency')
  ) {
    throw new Error('Presence notification update has invalid fields.')
  }
  const request = value as Record<string, unknown>
  if (
    !Number.isSafeInteger(request.expectedRevision)
    || (request.expectedRevision as number) < 0
  ) {
    throw new Error('Presence notification revision is invalid.')
  }
  if (typeof request.completionNotifications !== 'boolean') {
    throw new Error('Reply notification preference is invalid.')
  }
  if (!isPresenceReminderFrequency(request.reminderFrequency)) {
    throw new Error('Presence reminder frequency is invalid.')
  }
  return Object.freeze({
    expectedRevision: request.expectedRevision as number,
    completionNotifications: request.completionNotifications,
    reminderFrequency: request.reminderFrequency,
  })
}
