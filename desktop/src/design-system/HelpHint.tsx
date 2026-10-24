/** Show supplementary help on demand without occupying the surrounding form or panel. */

import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { t, useLocale } from '../i18n/LocaleProvider.tsx'
import { Icon } from './Icon.tsx'
import './HelpHint.css'

interface HelpHintProps {
  children: string
  id?: string
  label?: string
}

interface HelpAnchor {
  left: number
  top: number
  above: boolean
  maxHeight: number
  host: Element
}

/** Place help in the larger client-area gap, keeping native drag regions outside the popup. */
function positionHelp(button: HTMLButtonElement): HelpAnchor {
  const bounds = button.getBoundingClientRect()
  const width = Math.min(320, window.innerWidth - 24)
  // Read rendered geometry rather than a fixed caption height: zoom changes
  // CSS pixels and fullscreen removes the title strip without a new component.
  const titlebarBottom = document.querySelector('.desktop-titlebar')?.getBoundingClientRect().bottom ?? 0
  const topLimit = Math.max(0, titlebarBottom) + 12
  const aboveTop = Math.min(bounds.top - 8, window.innerHeight - 12)
  const belowTop = Math.max(bounds.bottom + 8, topLimit)
  const aboveSpace = Math.max(0, aboveTop - topLimit)
  const belowSpace = Math.max(0, window.innerHeight - belowTop - 12)
  const above = aboveSpace > belowSpace
  return {
    left: Math.max(12, Math.min(bounds.left, window.innerWidth - width - 12)),
    top: above ? aboveTop : belowTop,
    above,
    maxHeight: above ? aboveSpace : belowSpace,
    // A native dialog's top layer must also own its help portal.
    host: button.closest('dialog[open]') ?? document.body,
  }
}

/** Keep optional guidance available to pointer, keyboard, and touch users, including inside dialogs. */
export function HelpHint({ children, id, label }: HelpHintProps) {
  useLocale()
  const generatedId = useId()
  const descriptionId = id ?? `${generatedId}-help`
  const trigger = useRef<HTMLButtonElement>(null)
  const popup = useRef<HTMLSpanElement>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [anchor, setAnchor] = useState<HelpAnchor | null>(null)

  function clearClose() {
    clearTimeout(closeTimer.current)
  }

  function show() {
    clearClose()
    const button = trigger.current
    if (!button) return
    document.dispatchEvent(new CustomEvent('elysia-help-open', { detail: descriptionId }))
    setAnchor(positionHelp(button))
  }

  function deferClose() {
    clearClose()
    closeTimer.current = setTimeout(() => {
      if (document.activeElement !== trigger.current) setAnchor(null)
    }, 120)
  }

  useEffect(() => () => { clearTimeout(closeTimer.current) }, [])
  useEffect(() => {
    if (!anchor) return
    const dismiss = () => { clearTimeout(closeTimer.current); setAnchor(null) }
    const anotherHelp = (event: Event) => {
      if (event instanceof CustomEvent && event.detail !== descriptionId) dismiss()
    }
    const keydown = (event: KeyboardEvent) => {
      const content = popup.current
      if (document.activeElement === trigger.current && content
          && content.scrollHeight > content.clientHeight && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const offsets: Record<string, number> = { ArrowDown: 40, ArrowUp: -40,
          PageDown: content.clientHeight, PageUp: -content.clientHeight,
          Home: -content.scrollHeight, End: content.scrollHeight }
        if (event.key in offsets) {
          // Help stays outside the tab order, but long text still needs a
          // keyboard scrolling path that does not move its owning form.
          event.preventDefault()
          event.stopPropagation()
          content.scrollTop += offsets[event.key]
          return
        }
      }
      if (event.key !== 'Escape') return
      // Dismissing help must not also close the containing modal or discard a draft.
      event.preventDefault()
      event.stopPropagation()
      dismiss()
    }
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !trigger.current?.contains(event.target) && !popup.current?.contains(event.target)) dismiss()
    }
    const scroll = (event: Event) => {
      if (event.target instanceof Node && popup.current?.contains(event.target)) return
      const button = trigger.current
      if (!button) return
      // Focusing a low field can deliver a deferred native scroll after the
      // click opened help. Follow the anchor rather than immediately hiding
      // the explanation the user just requested. Visibility observation still
      // dismisses help when its owner leaves the scrolling viewport.
      setAnchor(positionHelp(button))
    }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => !entry.isIntersecting)) dismiss()
    })
    if (trigger.current) observer.observe(trigger.current)
    window.addEventListener('resize', dismiss)
    document.addEventListener('scroll', scroll, true)
    document.addEventListener('keydown', keydown, true)
    document.addEventListener('pointerdown', outside, true)
    document.addEventListener('elysia-help-open', anotherHelp)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', dismiss)
      document.removeEventListener('scroll', scroll, true)
      document.removeEventListener('keydown', keydown, true)
      document.removeEventListener('pointerdown', outside, true)
      document.removeEventListener('elysia-help-open', anotherHelp)
    }
  }, [anchor, descriptionId])

  const tooltip = <span ref={popup} id={descriptionId} role="tooltip" hidden={anchor === null}
    className="help-hint-popup" onPointerEnter={clearClose} onPointerLeave={deferClose}
    style={anchor ? { left: anchor.left, top: anchor.above ? undefined : anchor.top,
      bottom: anchor.above ? window.innerHeight - anchor.top : undefined,
      maxHeight: `min(20rem, calc(50vh - 32px), ${anchor.maxHeight}px)` } : undefined}>{children}</span>

  return <span className="help-hint">
    <button ref={trigger} type="button" className="help-hint-trigger" aria-label={label ? t('Help: {label}', { label }) : t('More information')}
      aria-describedby={descriptionId} onPointerEnter={show} onPointerLeave={deferClose}
      onFocus={show} onBlur={deferClose} onClick={event => {
        // Some fields use an enclosing label; help must never toggle that field.
        event.preventDefault()
        event.stopPropagation()
        show()
      }}><Icon name="info" /></button>
    {createPortal(tooltip, anchor?.host ?? document.body)}
  </span>
}
