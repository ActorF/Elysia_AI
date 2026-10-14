/** Shared compact tool workspaces with accessible disclosure, keyboard tabs, and persistent operation owners. */

import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon.tsx'
import './ToolPanel.css'

interface ToolPanelFrameProps {
  id: string
  title: string
  description: string
  icon: IconName
  ariaLabel: string
  open: boolean
  busy?: boolean
  status?: ReactNode
  actions?: ReactNode
  className?: string
  children: ReactNode
  /** Change visibility without retiring native requests owned by the contents. */
  onToggle(): void
}

/** Give tools one quiet disclosure header while retaining their mounted request and cancellation lifecycles. */
export function ToolPanelFrame({ id, title, description, icon, ariaLabel, open, busy = false,
  status, actions, className = '', children, onToggle }: ToolPanelFrameProps) {
  return <section className={`tool-panel ${className}`} aria-label={ariaLabel} aria-busy={busy} data-open={open}>
    <div className="tool-panel-header">
      <button type="button" className="tool-panel-toggle" aria-label={title} aria-expanded={open}
        aria-controls={`${id}-body`} onClick={onToggle}>
        <span className="tool-panel-symbol"><Icon name={icon} /></span>
        <span className="tool-panel-heading"><strong>{title}</strong><span>{description}</span></span>
        <Icon name="chevron" className="tool-panel-chevron" />
      </button>
      {status !== undefined && <span className="tool-panel-status">{busy && <span className="tool-busy-dot" aria-hidden="true" />}{status}</span>}
      {actions !== undefined && <div className="tool-panel-header-actions">{actions}</div>}
    </div>
    {/* Visibility is presentation only. Unmounting a child here would lose its
        in-flight cancellation handles and could release shared capacity early. */}
    <div id={`${id}-body`} className="tool-panel-body" hidden={!open}>{children}</div>
  </section>
}

interface ToolTabsProps<Value extends string> {
  idPrefix: string
  label: string
  value: Value
  items: readonly { value: Value; label: string; count?: number }[]
  /** Select an already mounted view; selection itself must not perform work. */
  onValueChange(value: Value): void
}

/** Navigate tool views with a single tab stop and the standard arrow, Home, and End keys. */
export function ToolTabs<Value extends string>({ idPrefix, label, value, items, onValueChange }: ToolTabsProps<Value>) {
  return <div className="tool-tabs" role="tablist" aria-label={label}>
    {items.map((item, index) => <button key={item.value} type="button" role="tab"
      id={`${idPrefix}-${item.value}-tab`} aria-controls={`${idPrefix}-${item.value}-panel`}
      aria-selected={value === item.value} tabIndex={value === item.value ? 0 : -1}
      onClick={() => { onValueChange(item.value) }} onKeyDown={event => {
        let next: number
        if (event.key === 'ArrowRight') next = (index + 1) % items.length
        else if (event.key === 'ArrowLeft') next = (index + items.length - 1) % items.length
        else if (event.key === 'Home') next = 0
        else if (event.key === 'End') next = items.length - 1
        else return
        event.preventDefault()
        onValueChange(items[next].value)
        const buttons = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
        buttons?.[next]?.focus()
      }}>
      {item.label}{item.count !== undefined && item.count > 0 && <span className="tool-tab-count">{item.count}</span>}
    </button>)}
  </div>
}

interface ToolTabPanelProps {
  idPrefix: string
  value: string
  activeValue: string
  children: ReactNode
}

/** Hide inactive tools without unmounting their pending requests, reviewed drafts, or cancellation owners. */
export function ToolTabPanel({ idPrefix, value, activeValue, children }: ToolTabPanelProps) {
  return <div className="tool-tab-panel" id={`${idPrefix}-${value}-panel`} role="tabpanel"
    aria-labelledby={`${idPrefix}-${value}-tab`} tabIndex={0} hidden={value !== activeValue}>{children}</div>
}
