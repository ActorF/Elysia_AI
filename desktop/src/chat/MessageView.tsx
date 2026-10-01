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
  useId,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { Icon } from '../design-system/Icon.tsx'
import type {
  ChatMessage,
  RetryEditDraft,
  RetryableChatPair,
} from './types.ts'
import '../knowledge/Knowledge.css'

interface MessageViewProps {
  message: ChatMessage
  retryEditDraft: RetryEditDraft | null
  retryPair: RetryableChatPair | null
  retryDisabled: boolean
  onBeginRetryEdit(pair: RetryableChatPair): void
  onCancelRetryEdit(): void
  onCopy(text: string): Promise<void>
  onOpenExternalUrl(url: string): Promise<void>
  onRetry(pair: RetryableChatPair, message?: string): boolean
  onRetryEditChange(pair: RetryableChatPair, message: string): void
}

function formatAttachmentBytes(sizeBytes: number): string {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`
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

type GroundedAnswer = NonNullable<ChatMessage['groundedAnswer']>
type GroundedCitation = GroundedAnswer['citations'][number]

function statementKindLabel(kind: GroundedAnswer['statements'][number]['kind']): string {
  switch (kind) {
    case 'source_fact':
      return 'Source fact'
    case 'model_summary':
      return 'Model summary'
    case 'inference':
      return 'Inference'
  }
}

function citationLocationLabel(citation: GroundedCitation): string {
  const labels: string[] = []
  if (citation.pageNumber !== null) {
    labels.push(`Page ${citation.pageNumber}`)
  }
  for (const [index, location] of citation.locations.entries()) {
    const locationLabels = [`Block ${location.blockOrdinal + 1}`]
    if (location.kind === 'table') {
      locationLabels.push(
        `Row ${location.rowIndex + 1}, column ${location.columnIndex + 1}`,
      )
    }
    locationLabels.push(
      `Characters ${location.sourceStartCodePoint}–${location.sourceEndCodePoint}`,
    )
    labels.push(
      citation.locations.length > 1
        ? `Location ${index + 1}: ${locationLabels.join(' · ')}`
        : locationLabels.join(' · '),
    )
  }
  return labels.join(' · ')
}

interface GroundedAnswerViewProps {
  answer: GroundedAnswer
}

/** Render a closed grounded answer without converting citations into web links. */
function GroundedAnswerView({ answer }: GroundedAnswerViewProps) {
  const [activeCitationId, setActiveCitationId] = useState<string | null>(null)
  const detailRef = useRef<HTMLElement | null>(null)
  const detailId = useId()
  const citationById = new Map(
    answer.citations.map((citation) => [citation.citationId, citation]),
  )
  const activeCitation = activeCitationId === null
    ? null
    : citationById.get(activeCitationId) ?? null

  useEffect(() => {
    if (activeCitation !== null) {
      detailRef.current?.focus({ preventScroll: true })
    }
  }, [activeCitation])

  if (answer.status === 'insufficient_evidence') {
    return (
      <div className="grounded-answer grounded-answer-insufficient" role="status">
        <strong>Project Sources did not produce an answer.</strong>
        <span>
          {answer.contextPassageCount === 0
            ? 'No relevant passages were found in the current Project corpus.'
            : 'Relevant passages were found, but they did not support a reliable answer.'}
        </span>
      </div>
    )
  }

  return (
    <div className="grounded-answer">
      <ol className="grounded-statement-list" aria-label="Grounded answer statements">
        {answer.statements.map((statement) => (
          <li key={statement.statementId} className="grounded-statement">
            <span className={`grounded-kind grounded-kind-${statement.kind}`}>
              {statementKindLabel(statement.kind)}
            </span>
            <p>{statement.text}</p>
            <div className="grounded-citation-buttons" aria-label="Citations">
              {statement.citationIds.map((citationId) => {
                const citation = citationById.get(citationId)
                if (citation === undefined) {
                  return null
                }
                const citationNumber = answer.citations.findIndex(
                  (item) => item.citationId === citationId,
                ) + 1
                return (
                  <button
                    key={citationId}
                    type="button"
                    className="grounded-citation-button"
                    aria-controls={detailId}
                    aria-expanded={activeCitationId === citationId}
                    onClick={() => {
                      setActiveCitationId(citationId)
                    }}
                  >
                    [{citationNumber}] {citation.fileName}
                  </button>
                )
              })}
            </div>
          </li>
        ))}
      </ol>

      {activeCitation !== null && (
        <section
          ref={detailRef}
          id={detailId}
          className="grounded-citation-detail"
          aria-label={`Citation from ${activeCitation.fileName}`}
          tabIndex={-1}
        >
          <div className="grounded-citation-heading">
            <div>
              <strong>{activeCitation.fileName}</strong>
              <span>{activeCitation.mediaType}</span>
            </div>
            <button
              type="button"
              className="grounded-citation-close"
              aria-label="Close citation details"
              onClick={() => { setActiveCitationId(null) }}
            >
              <Icon name="close" />
            </button>
          </div>
          <p className="grounded-citation-location">
            {citationLocationLabel(activeCitation)}
          </p>
          <blockquote>{activeCitation.excerpt}</blockquote>
        </section>
      )}
    </div>
  )
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
  retryEditDraft,
  retryPair,
  retryDisabled,
  onBeginRetryEdit,
  onCancelRetryEdit,
  onCopy,
  onOpenExternalUrl,
  onRetry,
  onRetryEditChange,
}: MessageViewProps) {
  const editRef = useRef<HTMLTextAreaElement | null>(null)
  const activeEditDraft = retryPair !== null
    && message.role === 'assistant'
    && message.id === retryPair.assistantMessageId
    && retryEditDraft?.chatId === retryPair.chatId
    && retryEditDraft.userMessageId === retryPair.userMessageId
    && retryEditDraft.assistantMessageId === retryPair.assistantMessageId
    ? retryEditDraft
    : null
  const editing = activeEditDraft !== null && !activeEditDraft.submitted
  const anotherEditDraftExists = retryEditDraft !== null && activeEditDraft === null

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

        {isAssistant && message.groundedAnswer !== undefined ? (
          <GroundedAnswerView answer={message.groundedAnswer} />
        ) : isAssistant ? (
          <AssistantMarkdown
            text={message.text}
            onCopy={onCopy}
            onOpenExternalUrl={onOpenExternalUrl}
          />
        ) : (
          <p className="message-text">{message.text}</p>
        )}

        {message.attachments.length > 0 && (
          <div className="message-attachments">
            <ul
              className="message-attachment-list"
              aria-label={`Attachments in ${isAssistant ? 'Elysia message' : 'your message'}`}
            >
              {message.attachments.map((attachment) => (
                <li key={attachment.attachmentId}>
                  <Icon name="file" />
                  <span>
                    <strong title={attachment.fileName}>{attachment.fileName}</strong>
                    <small>
                      {attachment.mediaType} · {formatAttachmentBytes(attachment.sizeBytes)}
                    </small>
                  </span>
                </li>
              ))}
            </ul>
            <p>Stored locally · File contents are not read or indexed yet.</p>
          </div>
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
          {pairActionsAvailable && activeEditDraft === null && (
            <>
              <button
                type="button"
                className="message-action"
                disabled={retryDisabled || anotherEditDraftExists}
                onClick={() => { onRetry(retryPair) }}
              >
                <Icon name="refresh" />
                <span>Regenerate</span>
              </button>
              <button
                type="button"
                className="message-action"
                disabled={retryDisabled || anotherEditDraftExists}
                onClick={() => {
                  onBeginRetryEdit(retryPair)
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
              if (activeEditDraft.text.trim() === '') {
                return
              }
              onRetry(retryPair, activeEditDraft.text)
            }}
          >
            <label htmlFor={`edit-message-${message.id}`}>
              Edit your last message
            </label>
            <textarea
              ref={editRef}
              id={`edit-message-${message.id}`}
              value={activeEditDraft.text}
              rows={3}
              maxLength={1_000_000}
              disabled={retryDisabled}
              onChange={(event) => {
                onRetryEditChange(retryPair, event.target.value)
              }}
            />
            <div className="message-edit-actions">
              <button
                type="button"
                className="message-edit-button secondary"
                onClick={onCancelRetryEdit}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="message-edit-button primary"
                disabled={retryDisabled || activeEditDraft.text.trim() === ''}
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
