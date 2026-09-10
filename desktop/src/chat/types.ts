/** Define renderer-only Chat presentation types; persisted data stays in Python. */

import type { InlineAlertTone } from '../design-system/Feedback.tsx'
import type { ChatAttachment } from '../../electron/contracts.ts'

export type ChatMessageState =
  | 'streaming'
  | 'complete'
  | 'error'
  | 'cancelled'

/** A single locally rendered message and its streaming lifecycle state. */
export interface ChatMessage {
  attachments: ChatAttachment[]
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

/** A renderer-owned edit that must survive retry failures and window reloads. */
export interface RetryEditDraft {
  chatId: string
  userMessageId: string
  assistantMessageId: string
  text: string
  submitted: boolean
}

/** Contextual feedback with explicit semantics for visual and live-region use. */
export interface ChatNotice {
  message: string
  tone: InlineAlertTone
}
