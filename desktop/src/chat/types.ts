/** Define renderer-only Chat presentation types; persisted data stays in Python. */

import type { InlineAlertTone } from '../design-system/Feedback.tsx'

export type ChatMessageState =
  | 'streaming'
  | 'complete'
  | 'error'
  | 'cancelled'

/** A single locally rendered message and its streaming lifecycle state. */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  state: ChatMessageState
  /** True only when Python has returned this exact record from persistence. */
  persisted: boolean
}

/** The only persisted turn Python currently permits the user to regenerate. */
export interface RetryableChatPair {
  chatId: string
  userMessageId: string
  assistantMessageId: string
  userText: string
  assistantText: string
}

/** Contextual feedback with explicit semantics for visual and live-region use. */
export interface ChatNotice {
  message: string
  tone: InlineAlertTone
}
