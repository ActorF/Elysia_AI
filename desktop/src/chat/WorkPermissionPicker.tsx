/**
 * @fileoverview Present Work permission levels in a themed, viewport-bound
 * combobox. Navigation previews choices; only an explicit selection changes access.
 */

import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from '../i18n/LocaleProvider.tsx'
import { createPortal } from 'react-dom'

import type { WorkTurnOptions } from '../../electron/work-agent-protocol.ts'
import { Icon } from '../design-system/Icon.tsx'
import './WorkPermissionPicker.css'

type Permission = WorkTurnOptions['permission']

interface WorkPermissionPickerProps {
  value: Permission
  disabled: boolean
  /** Commit only the permission explicitly chosen by the user. */
  onChange(value: Permission): void
}

const PERMISSIONS: ReadonlyArray<{ value: Permission; label: string; description: string }> = [
  { value: 'none', label: '仅对话', description: '不使用工具' },
  { value: 'read', label: '只读', description: '查找文件、查看应用和音量' },
  { value: 'ask', label: '操作前询问', description: '每条 CMD 命令和桌面操作前由你确认' },
  { value: 'auto', label: '允许桌面操作', description: '直接执行已支持的桌面工具' },
  { value: 'full', label: '允许 CMD 命令', description: '按当前 Windows 用户权限执行命令，可读写文件、运行程序和联网' },
]

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
  const left = viewport?.offsetLeft ?? 0
  const top = viewport?.offsetTop ?? 0
  const width = viewport?.width ?? window.innerWidth
  const height = viewport?.height ?? window.innerHeight
  const gutter = 12
  const gap = 8
  const popupWidth = Math.max(0, Math.min(280, width - gutter * 2))
  const above = anchor.top - top - gutter - gap
  const below = top + height - anchor.bottom - gutter - gap
  // Anchor the nearer popup edge so zoom, wrapped controls, and tall drafts
  // cannot push the permission choices beyond the visible application window.
  const openAbove = above >= 292 || above >= below
  return {
    left: Math.max(left + gutter, Math.min(anchor.left, left + width - gutter - popupWidth)),
    ...(openAbove ? { bottom: window.innerHeight - anchor.top + gap } : { top: anchor.bottom + gap }),
    width: popupWidth,
    maxHeight: Math.max(0, Math.min(340, openAbove ? above : below)),
  }
}

/**
 * Select one per-turn access level with pointer or Enter/Space confirmation.
 * Arrow navigation, dismissal, and Tab never grant a different permission.
 */
export function WorkPermissionPicker({ value, disabled, onChange }: WorkPermissionPickerProps) {
  const { t } = useTranslation()
  const listboxId = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const selectedIndex = PERMISSIONS.findIndex((permission) => permission.value === value)
  const [interaction, setInteraction] = useState({ disabled, open: false })
  const [activeIndex, setActiveIndex] = useState(selectedIndex)
  const [placement, setPlacement] = useState<PopupPlacement | null>(null)
  const open = interaction.open && !disabled

  // A generation freezes its options. Closing on the disabling render prevents
  // a stale menu from returning when the next user turn becomes available.
  if (interaction.disabled !== disabled) setInteraction({ disabled, open: false })

  useLayoutEffect(() => {
    if (!open) return
    function updatePlacement(): void {
      if (triggerRef.current !== null) setPlacement(measurePopup(triggerRef.current))
    }
    function handleOutsidePointer(event: PointerEvent): void {
      if (!(event.target instanceof Node)
        || triggerRef.current?.contains(event.target)
        || popupRef.current?.contains(event.target)) return
      setInteraction({ disabled, open: false })
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
  }, [disabled, open])

  useLayoutEffect(() => {
    if (open) document.getElementById(`${listboxId}-${activeIndex}`)?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, listboxId, open])

  function openPopup(index = selectedIndex): void {
    if (disabled || triggerRef.current === null) return
    setPlacement(measurePopup(triggerRef.current))
    setActiveIndex(index)
    setInteraction({ disabled, open: true })
  }

  function commit(index: number): void {
    setInteraction({ disabled, open: false })
    triggerRef.current?.focus()
    const permission = PERMISSIONS[index]
    if (!disabled && permission !== undefined && permission.value !== value) onChange(permission.value)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>): void {
    if (disabled || event.nativeEvent.isComposing) return
    if ((event.key === 'Escape' || event.key === 'Tab') && open) {
      // Leaving the picker must not silently escalate access after a user merely
      // inspected an option. Tab keeps its ordinary focus movement.
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
      }
      setInteraction({ disabled, open: false })
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (open) commit(activeIndex)
      else openPopup()
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) openPopup()
      else setActiveIndex((activeIndex + (event.key === 'ArrowDown' ? 1 : -1)
        + PERMISSIONS.length) % PERMISSIONS.length)
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const index = event.key === 'Home' ? 0 : PERMISSIONS.length - 1
      if (!open) openPopup(index)
      else setActiveIndex(index)
    }
  }

  return (
    <div className="work-permission-picker">
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        className="work-permission-trigger"
        aria-label={t("Work permissions")}
        title={t(PERMISSIONS[selectedIndex].description)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={open ? `${listboxId}-${activeIndex}` : undefined}
        disabled={disabled}
        onClick={() => {
          if (open) setInteraction({ disabled, open: false })
          else openPopup()
        }}
        onKeyDown={handleKeyDown}
        onBlur={() => setInteraction({ disabled, open: false })}
      >
        <Icon name="shield" />
        <span>{t(PERMISSIONS[selectedIndex].label)}</span>
        <Icon name="chevron" className="work-permission-chevron" />
      </button>
      {open && placement !== null && createPortal(
        <div
          ref={popupRef}
          className="work-permission-popover"
          style={placement}
          // Preserve combobox focus so keyboard and pointer use the same active
          // descendant without introducing a focusable element for each option.
          onPointerDown={(event) => event.preventDefault()}
        >
          <div className="work-permission-heading" aria-hidden="true">{t("工具权限")}</div>
          <div id={listboxId} role="listbox" aria-label={t("Work permission levels")} className="work-permission-options">
            {PERMISSIONS.map((permission, index) => (
              <div
                id={`${listboxId}-${index}`}
                key={permission.value}
                role="option"
                aria-label={t(permission.label)}
                aria-selected={permission.value === value}
                aria-describedby={`${listboxId}-${index}-description`}
                className={`work-permission-option${index === activeIndex ? ' is-active' : ''}`}
                onPointerMove={(event) => {
                  if (event.pointerType !== 'touch') setActiveIndex(index)
                }}
                onClick={() => commit(index)}
              >
                <span className="work-permission-copy">
                  <span>{t(permission.label)}</span>
                  <span id={`${listboxId}-${index}-description`} className="work-permission-description">{t(permission.description)}</span>
                </span>
                {permission.value === value && <Icon name="check" />}
              </div>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
