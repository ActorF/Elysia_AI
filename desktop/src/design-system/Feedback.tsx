/**
 * Provide consistent, accessible loading, empty, and inline feedback states.
 */

import { t, useLocale } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'

import { useId, type ReactNode } from 'react'

import { Icon, type IconName } from './Icon'

export interface FeedbackAction {
  label: string
  /** Run the feedback surface's optional next or recovery action. */
  onClick(): void
  disabled?: boolean
}

interface FeedbackBaseProps {
  className?: string
}

export interface LoadingStateProps extends FeedbackBaseProps {
  title?: string
  description?: string
}

export interface EmptyStateProps extends FeedbackBaseProps {
  title: string
  description?: string
  /** Omit to use the default mark, or pass null for a text-only empty state. */
  icon?: IconName | null
  action?: FeedbackAction
}

export type InlineAlertTone = 'error' | 'info' | 'success' | 'warning'

export interface InlineAlertProps extends FeedbackBaseProps {
  children: ReactNode
  tone?: InlineAlertTone
  title?: string
  action?: FeedbackAction
  dismissLabel?: string
  /** Dismiss the transient alert when its close control is activated. */
  onDismiss?(): void
}

function classNames(baseName: string, className?: string): string {
  return className === undefined ? baseName : `${baseName} ${className}`
}

/** Announce an in-progress operation without repeatedly interrupting the user. */
export function LoadingState({
  title = 'Loading',
  description,
  className,
}: LoadingStateProps) {
  useLocale()
  return (
    <section
      className={classNames('feedback-state loading-state', className)}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      aria-busy="true"
    >
      <Icon name="sparkles" className="feedback-state-icon loading-state-icon" />
      <div className="feedback-state-copy">
        <strong>{t(title)}</strong>
        {description !== undefined && <p>{t(description)}</p>}
      </div>
    </section>
  )
}

/** Explain an empty collection and optionally offer one clear next action. */
export function EmptyState({
  title,
  description,
  icon = 'sparkles',
  action,
  className,
}: EmptyStateProps) {
  useLocale()
  const titleId = useId()

  return (
    <section
      className={classNames('feedback-state empty-state', className)}
      aria-labelledby={titleId}
    >
      {icon !== null && (
        <Icon name={icon} className="feedback-state-icon empty-state-icon" />
      )}
      <div className="feedback-state-copy">
        <h2 id={titleId}>{t(title)}</h2>
        {description !== undefined && <p>{t(description)}</p>}
      </div>
      {action !== undefined && (
        <button
          type="button"
          className="feedback-action"
          onClick={action.onClick}
          disabled={action.disabled}
        >
          {t(action.label)}
        </button>
      )}
    </section>
  )
}

/**
 * Present transient contextual feedback. Errors interrupt immediately, while
 * informational, success, and warning updates use a polite live region.
 */
export function InlineAlert({
  children,
  tone = 'info',
  title,
  action,
  dismissLabel = 'Dismiss notification',
  onDismiss,
  className,
}: InlineAlertProps) {
  useLocale()
  const isError = tone === 'error'

  return (
    <div
      className={classNames(`inline-alert inline-alert-${tone}`, className)}
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
      aria-atomic="true"
    >
      <Icon name="info" className="inline-alert-icon" />
      <div className="inline-alert-copy">
        {title !== undefined && <strong>{t(title)}</strong>}
        <div className="inline-alert-message">{typeof children === 'string' ? formatUiError(children) : children}</div>
      </div>
      {action !== undefined && (
        <button
          type="button"
          className="inline-alert-action"
          onClick={action.onClick}
          disabled={action.disabled}
        >
          {t(action.label)}
        </button>
      )}
      {onDismiss !== undefined && (
        <button
          type="button"
          className="inline-alert-dismiss"
          onClick={onDismiss}
          aria-label={t(dismissLabel)}
        >
          <Icon name="close" />
        </button>
      )}
    </div>
  )
}
