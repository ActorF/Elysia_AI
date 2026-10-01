/**
 * Decide when optional fixed-copy system notifications may be delivered.
 *
 * These pure helpers keep cadence and attention checks independently testable.
 * They never create a notification, inspect conversation content, or schedule
 * background work after the desktop process exits.
 */

import type {
  PresenceNotificationRuntime,
  PresenceReminderFrequency,
} from './presence-notification-contracts.js'

/** Milliseconds between neutral reminders for each enabled closed cadence. */
export const PRESENCE_REMINDER_INTERVAL_MS: Readonly<Record<
  Exclude<PresenceReminderFrequency, 'off'>,
  number
>> = Object.freeze({
  daily: 24 * 60 * 60 * 1000,
  weekly: 7 * 24 * 60 * 60 * 1000,
})

/** Native-window and Backend activity used without exposing private content. */
export interface PresenceNotificationActivity {
  readonly shutdownStarted: boolean
  readonly windowExists: boolean
  readonly windowVisible: boolean
  readonly windowMinimized: boolean
  readonly windowFocused: boolean
  readonly backendReady: boolean
  readonly backendBusy: boolean
}

/** Minimal persisted intent used by notification-delivery policy. */
export interface PresenceNotificationIntent {
  readonly completionNotifications: boolean
  readonly reminderFrequency: PresenceReminderFrequency
  readonly runtime: PresenceNotificationRuntime
}

/**
 * Return the next reminder delay, or ``null`` when scheduling must stay off.
 *
 * A non-off preference must have an exact persisted anchor. Missing or invalid
 * anchors fail closed instead of manufacturing an immediate reminder. A clock
 * rollback naturally lengthens the interval and can never produce a burst.
 */
export function nextPresenceReminderDelayMs(
  frequency: PresenceReminderFrequency,
  lastReminderHandledAt: string | null,
  preferenceUpdatedAt: string | null,
  nowMs: number,
): number | null {
  if (frequency === 'off' || !Number.isFinite(nowMs)) {
    return null
  }
  const anchor = lastReminderHandledAt ?? preferenceUpdatedAt
  if (anchor === null) {
    return null
  }
  const anchorMs = Date.parse(anchor)
  if (!Number.isFinite(anchorMs)) {
    return null
  }
  const interval = PRESENCE_REMINDER_INTERVAL_MS[frequency]
  // A wall-clock rollback or future timestamp waits one ordinary interval;
  // it must not create an overflowing native timer or years-long stale state.
  if (anchorMs > nowMs) {
    return interval
  }
  return Math.max(0, anchorMs + interval - nowMs)
}

function mainWindowIsAttentive(
  activity: PresenceNotificationActivity,
): boolean {
  return activity.windowExists
    && activity.windowVisible
    && !activity.windowMinimized
    && activity.windowFocused
}

/** Allow a reply-ready notice only after opt-in and while the app is unattended. */
export function shouldDeliverCompletionNotification(
  intent: PresenceNotificationIntent,
  activity: PresenceNotificationActivity,
): boolean {
  return intent.completionNotifications
    && intent.runtime === 'available'
    && !activity.shutdownStarted
    && !activity.backendBusy
    && !mainWindowIsAttentive(activity)
}

/**
 * Allow a due presence reminder only while Elysia is idle and hidden.
 *
 * Merely switching to another foreground application is not enough to trigger
 * proactive contact. Main still consumes a due cadence while suppressed so a
 * reminder cannot appear immediately after the user leaves an active window.
 */
export function shouldDeliverPresenceReminder(
  intent: PresenceNotificationIntent,
  activity: PresenceNotificationActivity,
  reminderDue: boolean,
): boolean {
  return reminderDue
    && intent.reminderFrequency !== 'off'
    && intent.runtime === 'available'
    && !activity.shutdownStarted
    && activity.backendReady
    && !activity.backendBusy
    && (
      !activity.windowExists
      || !activity.windowVisible
      || activity.windowMinimized
    )
}
