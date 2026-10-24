/** Present the persistent Vision workspace as a native modal without moving or remounting the conversation. */

import { t, useTranslation } from '../i18n/LocaleProvider.tsx'
import { useEffect, useId, useRef, type ReactNode } from 'react'
import { Icon } from '../design-system/Icon.tsx'

interface VisionDialogProps {
  open: boolean
  busy: boolean
  children: ReactNode
  /** Hide presentation only; pending native operations retain their owners. */
  onClose(): void
}

/** Preserve focus and native request lifetimes while opening or hiding the image workspace. */
export function VisionDialog({ open, busy, children, onClose }: VisionDialogProps) {
  useTranslation()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const dialog = dialogRef.current
    if (dialog === null) return
    if (open && !dialog.open) dialog.showModal()
    else if (!open && dialog.open) dialog.close()
  }, [open])

  useEffect(() => {
    if (!open) return
    const handleKey = (event: KeyboardEvent): void => {
      const dialog = dialogRef.current
      if (dialog === null) return
      if (event.key === 'Escape') {
        // Help is portalled into this dialog. Let its document listener close
        // the open popup before a later Escape hides the entire workspace.
        if (dialog.querySelector('.help-hint-popup:not([hidden])') !== null) return
        // The workspace closes even while a provider drains. Consuming Escape
        // here keeps the Sidebar's lower-priority handler from also closing.
        event.preventDefault(); event.stopImmediatePropagation(); onClose()
      } else if (event.key === 'Tab') {
        const candidates = Array.from(dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [href], [tabindex="0"]',
        )).filter((element) => element.getClientRects().length > 0 && element.tabIndex >= 0)
        const first = candidates[0], last = candidates.at(-1)
        if (first === undefined || last === undefined) return
        if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {
          event.preventDefault(); event.stopImmediatePropagation()
          ;(event.shiftKey ? last : first).focus()
        }
      }
    }
    window.addEventListener('keydown', handleKey, true)
    return () => { window.removeEventListener('keydown', handleKey, true) }
  }, [open, onClose])

  return <dialog ref={dialogRef} className="vision-dialog" aria-labelledby={titleId} aria-busy={busy}
    onCancel={(event) => { event.preventDefault(); onClose() }} onMouseDown={(event) => {
      const bounds = event.currentTarget.getBoundingClientRect()
      if (event.target === event.currentTarget && (event.clientX < bounds.left || event.clientX > bounds.right
        || event.clientY < bounds.top || event.clientY > bounds.bottom)) onClose()
    }}>
    <div className="vision-dialog-header"><span className="vision-dialog-symbol"><Icon name="image" /></span><div><h2 id={titleId}>{t("Vision")}</h2></div>
      {busy && <span className="vision-dialog-status" role="status">{t("Working…")}</span>}
      <button type="button" className="icon-button" aria-label={t("Close Vision")} title={t("Close Vision")} onClick={onClose}><Icon name="close" /></button>
    </div>
    {children}
  </dialog>
}
