/** Define renderer-only Chat presentation types; persisted data stays in Python. */

import type { InlineAlertTone } from '../design-system/Feedback.tsx'

/** A single locally rendered message and its streaming lifecycle state. */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  state: 'streaming' | 'complete' | 'error'
}

/** Contextual feedback with explicit semantics for visual and live-region use. */
export interface ChatNotice {
  message: string
  tone: InlineAlertTone
}
