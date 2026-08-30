/**
 * Render safe conversation content and actions.
 *
 * User text stays literal. Assistant text supports GFM through react-markdown,
 * with raw HTML skipped and image nodes replaced before the browser can fetch
 * their source. Navigation and clipboard writes always cross DesktopApi.
 */

import {
  isValidElement,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { Icon } from '../design-system/Icon.tsx'
import type {
  ChatMessage,
  RetryableChatPair,
} from './types.ts'

interface MessageViewProps {
  message: ChatMessage
  retryPair: RetryableChatPair | null
  retryDisabled: boolean
  onCopy(text: string): Promise<void>
  onOpenExternalUrl(url: string): Promise<void>
  onRetry(pair: RetryableChatPair, message?: string): void
}

function nodeText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node)
  }
  if (Array.isArray(node)) {
    return node.map(nodeText).join('')
  }
  if (isValidElement<{ children?: ReactNode }>(node)) {
    return nodeText(node.props.children)
  }
  return ''
}

function safeExternalUrl(href: string | undefined): string | null {
  if (href === undefined) {
    return null
  }
  try {
    const parsed = new URL(href)
    if (
      (parsed.protocol === 'http:' || parsed.protocol === 'https:')
      && parsed.username === ''
      && parsed.password === ''
    ) {
      return parsed.href
    }
  } catch {
    // Relative, malformed, and non-web URLs stay inert in the renderer.
  }
  return null
}

function statusLabel(message: ChatMessage): string {
  switch (message.state) {
    case 'complete':
      return 'Complete'
    case 'streaming':
      return 'Generating'
    case 'error':
      return 'Reply interrupted'
    case 'cancelled':
      return 'Stopped'
  }
}

interface CopyButtonProps {
  label: string
  text: string
  onCopy(text: string): Promise<void>
}

function CopyButton({ label, text, onCopy }: CopyButtonProps) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle')
  const resetTimerRef = useRef<number | null>(null)

  useEffect(() => () => {
    if (resetTimerRef.current !== null) {
      window.clearTimeout(resetTimerRef.current)
    }
  }, [])

  async function copy(): Promise<void> {
    try {
      await onCopy(text)
      setCopyState('copied')
    } catch {
      setCopyState('error')
    }
    if (resetTimerRef.current !== null) {
      window.clearTimeout(resetTimerRef.current)
    }
    resetTimerRef.current = window.setTimeout(() => {
      setCopyState('idle')
      resetTimerRef.current = null
    }, 1_600)
  }

  const buttonLabel = copyState === 'copied'
    ? `${label} copied`
    : copyState === 'error'
      ? `${label} failed`
      : label
  const visibleLabel = copyState === 'copied'
    ? 'Copied'
    : copyState === 'error'
      ? 'Copy failed'
      : label

  return (
    <button
      type="button"
      className="message-action"
      aria-label={buttonLabel}
      title={visibleLabel}
      onClick={() => { void copy() }}
    >
      <Icon name={copyState === 'copied' ? 'check' : 'copy'} />
      <span>{visibleLabel}</span>
    </button>
  )
}

interface AssistantMarkdownProps {
  text: string
  onCopy(text: string): Promise<void>
  onOpenExternalUrl(url: string): Promise<void>
}

function AssistantMarkdown({
  text,
  onCopy,
  onOpenExternalUrl,
}: AssistantMarkdownProps) {
  const [linkError, setLinkError] = useState(false)

  return (
    <div className="message-markdown">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a({ href, children }) {
            const externalUrl = safeExternalUrl(href)
            if (externalUrl === null) {
              return <span className="unsafe-markdown-link">{children}</span>
            }
            return (
              <a
                href={externalUrl}
                onClick={(event) => {
                  event.preventDefault()
                  setLinkError(false)
                  void onOpenExternalUrl(externalUrl).catch(() => {
                    setLinkError(true)
                  })
                }}
              >
                {children}
              </a>
            )
          },
          img({ alt }) {
            return (
              <span className="blocked-markdown-image" role="note">
                {alt ? `Image blocked: ${alt}` : 'Remote image blocked'}
              </span>
            )
          },
          pre({ children }) {
            const code = nodeText(children).replace(/\n$/, '')
            return (
              <div className="code-block">
                <div className="code-block-toolbar">
                  <span>Code</span>
                  <CopyButton
                    label="Copy code"
                    text={code}
                    onCopy={onCopy}
                  />
                </div>
                <pre>{children}</pre>
              </div>
            )
          },
        }}
      >
        {text}
      </Markdown>
      {linkError && (
        <span className="message-action-error" role="status">
          Could not open this link.
        </span>
      )}
    </div>
  )
}

/** Render one user or assistant message with its complete lifecycle state. */
export function MessageView({
  message,
  retryPair,
  retryDisabled,
  onCopy,
  onOpenExternalUrl,
  onRetry,
}: MessageViewProps) {
  const [editing, setEditing] = useState(false)
  const [editedMessage, setEditedMessage] = useState('')
  const editRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    if (editing) {
      editRef.current?.focus()
      editRef.current?.select()
    }
  }, [editing])

  const status = statusLabel(message)
  const isAssistant = message.role === 'assistant'
  const pairActionsAvailable = (
    isAssistant
    && retryPair !== null
    && message.id === retryPair.assistantMessageId
    && message.state !== 'streaming'
  )

  return (
    <article
      className={`message ${message.role} message-${message.state}`}
      aria-label={isAssistant ? 'Message from Elysia' : 'Message from you'}
      data-message-id={message.id}
    >
      <div className="message-avatar" aria-hidden="true">
        {isAssistant ? 'E' : 'Y'}
      </div>
      <div className="message-body">
        <div className="message-header">
          <span className="message-author">{isAssistant ? 'Elysia' : 'You'}</span>
          <span
            className={`message-status message-status-${message.state}`}
            role="status"
          >
            {status}
          </span>
        </div>

        {isAssistant ? (
          <AssistantMarkdown
            text={message.text}
            onCopy={onCopy}
            onOpenExternalUrl={onOpenExternalUrl}
          />
        ) : (
          <p className="message-text">{message.text}</p>
        )}

        {message.state === 'streaming' && (
          <span className="stream-caret" aria-hidden="true" />
        )}

        <div className="message-actions">
          <CopyButton
            label="Copy message"
            text={message.text}
            onCopy={onCopy}
          />
          {pairActionsAvailable && (
            <>
              <button
                type="button"
                className="message-action"
                disabled={retryDisabled}
                onClick={() => { onRetry(retryPair) }}
              >
                <Icon name="refresh" />
                <span>Regenerate</span>
              </button>
              <button
                type="button"
                className="message-action"
                disabled={retryDisabled}
                onClick={() => {
                  setEditedMessage(retryPair.userText)
                  setEditing(true)
                }}
              >
                <Icon name="edit" />
                <span>Edit &amp; retry</span>
              </button>
            </>
          )}
        </div>

        {editing && retryPair !== null && (
          <form
            className="message-edit-form"
            aria-label="Edit and retry message"
            onSubmit={(event) => {
              event.preventDefault()
              if (editedMessage.trim() === '') {
                return
              }
              setEditing(false)
              onRetry(retryPair, editedMessage)
            }}
          >
            <label htmlFor={`edit-message-${message.id}`}>
              Edit your last message
            </label>
            <textarea
              ref={editRef}
              id={`edit-message-${message.id}`}
              value={editedMessage}
              rows={3}
              disabled={retryDisabled}
              onChange={(event) => { setEditedMessage(event.target.value) }}
            />
            <div className="message-edit-actions">
              <button
                type="button"
                className="message-edit-button secondary"
                onClick={() => {
                  setEditing(false)
                  setEditedMessage(retryPair.userText)
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="message-edit-button primary"
                disabled={retryDisabled || editedMessage.trim() === ''}
              >
                Retry edited message
              </button>
            </div>
          </form>
        )}
      </div>
    </article>
  )
}
