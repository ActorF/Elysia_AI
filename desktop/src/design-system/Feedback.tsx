/**
 * Provide consistent, accessible loading, empty, and inline feedback states.
 */

import { useId, type ReactNode } from 'react'

import { Icon, type IconName } from './Icon'

export interface FeedbackAction {
  label: string
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
  icon?: IconName
  action?: FeedbackAction
}

export type InlineAlertTone = 'error' | 'info' | 'success' | 'warning'

export interface InlineAlertProps extends FeedbackBaseProps {
  children: ReactNode
  tone?: InlineAlertTone
  title?: string
  action?: FeedbackAction
  dismissLabel?: string
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
        <strong>{title}</strong>
        {description !== undefined && <p>{description}</p>}
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
  const titleId = useId()

  return (
    <section
      className={classNames('feedback-state empty-state', className)}
      aria-labelledby={titleId}
    >
      <Icon name={icon} className="feedback-state-icon empty-state-icon" />
      <div className="feedback-state-copy">
        <h2 id={titleId}>{title}</h2>
        {description !== undefined && <p>{description}</p>}
      </div>
      {action !== undefined && (
        <button
          type="button"
          className="feedback-action"
          onClick={action.onClick}
          disabled={action.disabled}
        >
          {action.label}
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
        {title !== undefined && <strong>{title}</strong>}
        <div className="inline-alert-message">{children}</div>
      </div>
      {action !== undefined && (
        <button
          type="button"
          className="inline-alert-action"
          onClick={action.onClick}
          disabled={action.disabled}
        >
          {action.label}
        </button>
      )}
      {onDismiss !== undefined && (
        <button
          type="button"
          className="inline-alert-dismiss"
          onClick={onDismiss}
          aria-label={dismissLabel}
        >
          <Icon name="close" />
        </button>
      )}
    </div>
  )
}
