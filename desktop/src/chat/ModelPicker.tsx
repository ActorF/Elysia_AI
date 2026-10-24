/**
 * @fileoverview Render a themed select-only model combobox with a viewport-bound
 * listbox and keyboard navigation.
 */

import { t, useLocale } from '../i18n/LocaleProvider.tsx'

import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react'
import { createPortal } from 'react-dom'

import './ModelPicker.css'

interface ModelPickerProps {
  value: string
  options: string[]
  disabled: boolean
  onChange(value: string): void
}

interface PopupPlacement {
  left: number
  top?: number
  bottom?: number
  width: number
  maxHeight: number
}

function measurePopup(trigger: HTMLElement): PopupPlacement {
  const anchor = trigger.getBoundingClientRect()
  const viewport = window.visualViewport
  const viewportLeft = viewport?.offsetLeft ?? 0
  const viewportTop = viewport?.offsetTop ?? 0
  const viewportWidth = viewport?.width ?? window.innerWidth
  const viewportHeight = viewport?.height ?? window.innerHeight
  const gutter = 12
  const gap = 10
  const width = Math.min(304, viewportWidth - gutter * 2)
  const spaceAbove = anchor.top - viewportTop - gutter - gap
  const spaceBelow = viewportTop + viewportHeight - anchor.bottom - gutter - gap
  // The composer usually sits at the bottom. Fall below only when a tall draft
  // or a very small window leaves insufficient room for a usable list above it.
  const above = spaceAbove >= 160 || spaceAbove >= spaceBelow
  const maxHeight = Math.max(0, Math.min(336, above ? spaceAbove : spaceBelow))
  return {
    left: Math.max(viewportLeft + gutter, Math.min(
      anchor.left,
      viewportLeft + viewportWidth - gutter - width,
    )),
    // Anchor the panel edge instead of guessing its content height, so larger
    // text and long model lists cannot grow downward over the trigger.
    ...(above
      ? { bottom: window.innerHeight - anchor.top + gap }
      : { top: anchor.bottom + gap }),
    width,
    maxHeight,
  }
}

/**
 * Select one available model without changing it during navigation. Focus stays
 * on the combobox; Enter, Space, Tab, or a pointer selection commits the option.
 */
export function ModelPicker({ value, options, disabled, onChange }: ModelPickerProps) {
  useLocale()
  const listboxId = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const typeaheadRef = useRef({ text: '', time: 0 })
  const available = !disabled && options.length > 0
  const [interaction, setInteraction] = useState({ available, open: false })
  const [activeIndex, setActiveIndex] = useState(0)
  const [placement, setPlacement] = useState<PopupPlacement | null>(null)
  const open = interaction.open && available
  const boundedActiveIndex = Math.min(activeIndex, Math.max(0, options.length - 1))

  // Backend loading and generation can disable an already open picker. Clear
  // the interaction with that render so re-enabling never resurrects its popup.
  if (interaction.available !== available) {
    setInteraction({ available, open: false })
  }

  useLayoutEffect(() => {
    if (!open) return

    function updatePlacement(): void {
      if (triggerRef.current !== null) {
        setPlacement(measurePopup(triggerRef.current))
      }
    }

    function handleOutsidePointer(event: PointerEvent): void {
      if (!(event.target instanceof Node)) return
      if (triggerRef.current?.contains(event.target)
        || popupRef.current?.contains(event.target)) return
      setInteraction({ available, open: false })
    }

    const observer = new ResizeObserver(updatePlacement)
    if (triggerRef.current !== null) observer.observe(triggerRef.current)
    window.addEventListener('resize', updatePlacement)
    window.addEventListener('scroll', updatePlacement, true)
    window.visualViewport?.addEventListener('resize', updatePlacement)
    window.visualViewport?.addEventListener('scroll', updatePlacement)
    document.addEventListener('pointerdown', handleOutsidePointer, true)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updatePlacement)
      window.removeEventListener('scroll', updatePlacement, true)
      window.visualViewport?.removeEventListener('resize', updatePlacement)
      window.visualViewport?.removeEventListener('scroll', updatePlacement)
      document.removeEventListener('pointerdown', handleOutsidePointer, true)
    }
  }, [available, open, options.length])

  useLayoutEffect(() => {
    if (open) {
      document.getElementById(`${listboxId}-${boundedActiveIndex}`)
        ?.scrollIntoView({ block: 'nearest' })
    }
  }, [boundedActiveIndex, listboxId, open])

  function openPopup(index = Math.max(0, options.indexOf(value))): void {
    if (!available || triggerRef.current === null) return
    setPlacement(measurePopup(triggerRef.current))
    setActiveIndex(index)
    typeaheadRef.current = { text: '', time: 0 }
    setInteraction({ available, open: true })
  }

  function commit(index: number, restoreFocus = true): void {
    const option = options[index]
    setInteraction({ available, open: false })
    if (restoreFocus) triggerRef.current?.focus()
    if (available && option !== undefined && option !== value) onChange(option)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>): void {
    if (!available || event.nativeEvent.isComposing) return

    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      setInteraction({ available, open: false })
      return
    }
    if (event.key === 'Tab') {
      if (open) commit(boundedActiveIndex, false)
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (open) commit(boundedActiveIndex)
      else openPopup()
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) openPopup()
      else setActiveIndex((boundedActiveIndex + (event.key === 'ArrowDown' ? 1 : -1)
        + options.length) % options.length)
      return
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const index = event.key === 'Home' ? 0 : options.length - 1
      if (!open) openPopup(index)
      else setActiveIndex(index)
      return
    }
    if (event.key.length !== 1 || event.altKey || event.ctrlKey || event.metaKey) return

    event.preventDefault()
    const now = performance.now()
    const previous = typeaheadRef.current
    const search = (now - previous.time > 600 ? '' : previous.text)
      + event.key.toLocaleLowerCase()
    // Repeated initial letters cycle models while longer prefixes retain their
    // current match, matching familiar native-select typeahead behavior.
    const repeated = [...search].every((letter) => letter === search[0])
    const prefix = repeated ? search[0] : search
    const start = open ? boundedActiveIndex : Math.max(0, options.indexOf(value))
    const offset = repeated ? 1 : 0
    const match = Array.from({ length: options.length }, (_, index) => (
      start + index + offset
    ) % options.length).find((index) => options[index].toLocaleLowerCase().startsWith(prefix))
    if (!open) openPopup(match ?? start)
    else if (match !== undefined) setActiveIndex(match)
    typeaheadRef.current = { text: search, time: now }
  }

  return (
    <div className="model-picker elysia-model-picker">
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-label={t("AI model")}
        aria-haspopup="listbox"
        aria-disabled={!available}
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={open ? `${listboxId}-${boundedActiveIndex}` : undefined}
        className="elysia-model-trigger"
        // Native disabled drops focus during the asynchronous model restart,
        // breaking Enter focus and resetting Tab to the document's first item.
        // ARIA plus guarded handlers preserves the current focus without a
        // deferred restoration that could steal the user's next interaction.
        tabIndex={available ? 0 : -1}
        title={value || t("No model")}
        onClick={() => {
          if (open) setInteraction({ available, open: false })
          else openPopup()
        }}
        onKeyDown={handleKeyDown}
        onBlur={() => { setInteraction({ available, open: false }) }}
      >
        <span className="elysia-model-value">{value || t("No model")}</span>
        <svg className="elysia-model-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="m5 9 3-3 3 3" />
        </svg>
      </button>
      {open && placement !== null && createPortal(
        <div
          ref={popupRef}
          className="elysia-model-popover"
          style={placement}
          onPointerDown={(event) => {
            // Keep keyboard focus on the combobox for aria-activedescendant;
            // pointer options remain clickable without creating a second tab stop.
            event.preventDefault()
          }}
        >
          <div className="elysia-model-heading" aria-hidden="true">
            <span>{t("Choose a model")}</span>
            <span>{options.length} {t("available")}</span>
          </div>
          <div id={listboxId} role="listbox" aria-label={t("AI models")} className="elysia-model-options">
            {options.map((option, index) => (
              <div
                id={`${listboxId}-${index}`}
                key={option}
                role="option"
                aria-selected={option === value}
                className={`elysia-model-option${index === boundedActiveIndex ? ' is-active' : ''}`}
                title={option}
                onPointerMove={(event) => {
                  if (event.pointerType !== 'touch') setActiveIndex(index)
                }}
                onClick={() => { commit(index) }}
              >
                <span>{option}</span>
                {option === value && (
                  <svg className="elysia-model-check" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                    <path d="m5 10 3.2 3.2L15 6.5" />
                  </svg>
                )}
              </div>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
