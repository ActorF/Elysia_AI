/**
 * Render a small native modal for Chat renames and confirmed mutations.
 *
 * The dialog owns only focus and submission mechanics. Its caller keeps the
 * action state and performs every persisted operation through DesktopApi.
 */

import {
  useEffect,
  useId,
  useRef,
  type FormEvent,
  type ReactNode,
} from 'react'

const DIALOG_FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

interface ChatActionDialogProps {
  children?: ReactNode
  confirmLabel: string
  danger?: boolean
  description: ReactNode
  error?: string | null
  open: boolean
  pending: boolean
  title: string
  onCancel(): void
  onConfirm(): void
}

function focusableElements(dialog: HTMLDialogElement): HTMLElement[] {
  return Array.from(
    dialog.querySelectorAll<HTMLElement>(DIALOG_FOCUSABLE_SELECTOR),
  ).filter((element) => element.getClientRects().length > 0)
}

/**
 * Show one modal action, trap keyboard focus, and keep Escape from dismissing
 * a compact Sidebar that sits behind the higher-priority dialog.
 */
export function ChatActionDialog({
  children,
  confirmLabel,
  danger = false,
  description,
  error = null,
  open,
  pending,
  title,
  onCancel,
  onConfirm,
}: ChatActionDialogProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null)
  const titleId = useId()
  const descriptionId = useId()
  const errorId = useId()

  useEffect(() => {
    const dialog = dialogRef.current
    if (dialog === null) {
      return
    }

    if (!open) {
      if (dialog.open) {
        dialog.close()
      }
      return
    }

    if (!dialog.open) {
      dialog.showModal()
    }

    const focusFrame = window.requestAnimationFrame(() => {
      const preferredTarget = dialog.querySelector<HTMLElement>(
        '[data-dialog-initial-focus]',
      )
      ;(preferredTarget ?? focusableElements(dialog)[0] ?? dialog).focus({
        preventScroll: true,
      })
    })

    const keepFocusInsideDialog = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopImmediatePropagation()
        if (!pending) {
          onCancel()
        }
        return
      }
      if (event.key !== 'Tab') {
        return
      }

      const candidates = focusableElements(dialog)
      if (candidates.length === 0) {
        event.preventDefault()
        event.stopImmediatePropagation()
        dialog.focus({ preventScroll: true })
        return
      }

      const first = candidates[0]
      const last = candidates[candidates.length - 1]
      const activeElement = document.activeElement
      if (!dialog.contains(activeElement)) {
        event.preventDefault()
        event.stopImmediatePropagation()
        ;(event.shiftKey ? last : first).focus({ preventScroll: true })
      } else if (event.shiftKey && activeElement === first) {
        event.preventDefault()
        event.stopImmediatePropagation()
        last.focus({ preventScroll: true })
      } else if (!event.shiftKey && activeElement === last) {
        event.preventDefault()
        event.stopImmediatePropagation()
        first.focus({ preventScroll: true })
      }
    }

    // Window capture runs before AppShell's document capture focus trap.
    window.addEventListener('keydown', keepFocusInsideDialog, true)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      window.removeEventListener('keydown', keepFocusInsideDialog, true)
    }
  }, [onCancel, open, pending])

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!pending) {
      onConfirm()
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className="chat-action-dialog"
      aria-labelledby={titleId}
      aria-describedby={error === null
        ? descriptionId
        : `${descriptionId} ${errorId}`}
      aria-busy={pending}
      onCancel={(event) => {
        event.preventDefault()
        if (!pending) {
          onCancel()
        }
      }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) {
          onCancel()
        }
      }}
    >
      <form className="chat-action-dialog-card" onSubmit={handleSubmit}>
        <div className="chat-action-dialog-copy">
          <h2 id={titleId}>{title}</h2>
          <div id={descriptionId}>{description}</div>
        </div>

        {children}

        {error !== null && (
          <p className="chat-action-dialog-error" id={errorId} role="alert">
            {error}
          </p>
        )}

        <div className="chat-action-dialog-actions">
          <button
            type="button"
            className="dialog-button dialog-button-secondary"
            data-dialog-initial-focus={children === undefined ? true : undefined}
            disabled={pending}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="submit"
            className={'dialog-button' + (danger ? ' dialog-button-danger' : '')}
            disabled={pending}
          >
            {pending ? `${confirmLabel}…` : confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  )
}
