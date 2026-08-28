/**
 * Render the shared, dependency-free SVG icon set used by the desktop UI.
 *
 * Icons are decorative by default so callers do not accidentally duplicate a
 * nearby button label for screen-reader users. A caller may set
 * `aria-hidden={false}` and provide an accessible name when an icon carries
 * meaning on its own.
 */

import type { SVGProps } from 'react'

export type IconName =
  | 'archive'
  | 'arrow-left'
  | 'captions'
  | 'chat'
  | 'check'
  | 'chevron'
  | 'close'
  | 'file'
  | 'folder'
  | 'hangup'
  | 'info'
  | 'edit'
  | 'memory'
  | 'menu'
  | 'microphone'
  | 'monitor'
  | 'moon'
  | 'more'
  | 'panel'
  | 'phone'
  | 'pin'
  | 'plus'
  | 'search'
  | 'send'
  | 'settings'
  | 'sparkles'
  | 'sun'
  | 'trash'
  | 'voice'

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: IconName
}

/** Render one icon while allowing layout classes and standard SVG props. */
export function Icon({
  name,
  className,
  'aria-hidden': ariaHidden = true,
  ...svgProps
}: IconProps) {
  const commonProps = {
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }

  let content
  switch (name) {
    case 'archive':
      content = (
        <>
          <path d="M4 7h16v13H4zM3 4h18v4H3z" />
          <path d="M9 12h6" />
        </>
      )
      break
    case 'arrow-left':
      content = <path d="m14.5 5-7 7 7 7M8 12h12" />
      break
    case 'search':
      content = (
        <>
          <circle cx="11" cy="11" r="6.5" />
          <path d="m16 16 4 4" />
        </>
      )
      break
    case 'chat':
      content = <path d="M5 18.5 3.5 21v-5A8 8 0 1 1 7 19.2" />
      break
    case 'check':
      content = <path d="m5 12 4 4L19 6" />
      break
    case 'folder':
      content = <path d="M3.5 7.5h6l2-2h9v13h-17z" />
      break
    case 'memory':
      content = (
        <>
          <path d="M9 4.5A3.5 3.5 0 0 0 5.5 8v1A3 3 0 0 0 4 14.5 3.5 3.5 0 0 0 9 18" />
          <path d="M15 4.5A3.5 3.5 0 0 1 18.5 8v1a3 3 0 0 1 1.5 5.5 3.5 3.5 0 0 1-5 3.5M9 4.5v14M15 4.5v14M9 9h2M13 14h2" />
        </>
      )
      break
    case 'settings':
      content = (
        <>
          <circle cx="12" cy="12" r="3" />
          <path d="M19 13.5v-3l-2-.6-.6-1.5 1-1.8-2.1-2.1-1.8 1L12 5l-.6-2h-3l-.6 2-1.5.6-1.8-1-2.1 2.1 1 1.8L3 10l-2 .6v3l2 .6.6 1.5-1 1.8 2.1 2.1 1.8-1L8 19l.6 2h3l.6-2 1.5-.6 1.8 1 2.1-2.1-1-1.8.4-1.5z" />
        </>
      )
      break
    case 'plus':
      content = <path d="M12 5v14M5 12h14" />
      break
    case 'file':
      content = <path d="M8.5 12.5 13 8a3 3 0 0 1 4.2 4.2l-6 6a5 5 0 0 1-7.1-7.1l6.4-6.4" />
      break
    case 'microphone':
      content = (
        <>
          <rect x="8.5" y="3" width="7" height="12" rx="3.5" />
          <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6" />
        </>
      )
      break
    case 'voice':
      content = <path d="M4 14v-4M8 17V7M12 20V4M16 17V7M20 14v-4" />
      break
    case 'phone':
      content = <path d="M7.2 3.5 10 8l-2 2a15 15 0 0 0 6 6l2-2 4.5 2.8-.5 3.1c-.2.8-.9 1.4-1.7 1.4C9.7 20.5 3.5 14.3 2.7 5.7c0-.8.6-1.5 1.4-1.7z" />
      break
    case 'send':
      content = <path d="m4 12 16-8-6 16-2.5-6.5zM11.5 13.5 20 4" />
      break
    case 'panel':
      content = (
        <>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M15 4v16M10 9l-3 3 3 3" />
        </>
      )
      break
    case 'chevron':
      content = <path d="m9 6 6 6-6 6" />
      break
    case 'captions':
      content = (
        <>
          <rect x="3" y="5" width="18" height="14" rx="3" />
          <path d="M10 10a2.5 2.5 0 1 0 0 4M17 10a2.5 2.5 0 1 0 0 4" />
        </>
      )
      break
    case 'hangup':
      content = <path d="M4 15.5c4.7-4.7 11.3-4.7 16 0l-3 3-3-2v-2.2a9 9 0 0 0-4 0v2.2l-3 2z" />
      break
    case 'sparkles':
      content = <path d="m12 3 1.3 3.7L17 8l-3.7 1.3L12 13l-1.3-3.7L7 8l3.7-1.3zM18.5 14l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8zM5 13l.7 1.8 1.8.7-1.8.7L5 18l-.7-1.8-1.8-.7 1.8-.7z" />
      break
    case 'menu':
      content = <path d="M4 7h16M4 12h16M4 17h16" />
      break
    case 'close':
      content = <path d="m6 6 12 12M18 6 6 18" />
      break
    case 'sun':
      content = (
        <>
          <circle cx="12" cy="12" r="3.5" />
          <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4" />
        </>
      )
      break
    case 'moon':
      content = <path d="M20 15.2A8.5 8.5 0 0 1 8.8 4a8.5 8.5 0 1 0 11.2 11.2Z" />
      break
    case 'monitor':
      content = (
        <>
          <rect x="3" y="4" width="18" height="13" rx="2" />
          <path d="M8 21h8M12 17v4" />
        </>
      )
      break
    case 'info':
      content = (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 10.5V17" />
          <path d="M12 7h.01" />
        </>
      )
      break
    case 'edit':
      content = (
        <>
          <path d="M4 20h4L19 9l-4-4L4 16z" />
          <path d="m13.5 6.5 4 4" />
        </>
      )
      break
    case 'more':
      content = (
        <>
          <circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
          <circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" />
        </>
      )
      break
    case 'pin':
      content = (
        <>
          <path d="m9 4 6 0-1 6 3 3H7l3-3z" />
          <path d="M12 13v7" />
        </>
      )
      break
    case 'trash':
      content = (
        <>
          <path d="M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13" />
          <path d="M10 11v5M14 11v5" />
        </>
      )
      break
  }

  return (
    <svg
      {...commonProps}
      {...svgProps}
      className={className}
      aria-hidden={ariaHidden}
      focusable="false"
    >
      {content}
    </svg>
  )
}
