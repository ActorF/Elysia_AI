/**
 * Render safe conversation content and actions.
 *
 * User text stays literal. Assistant text supports GFM through react-markdown,
 * with raw HTML skipped and image nodes replaced before the browser can fetch
 * their source. Navigation and clipboard writes always cross DesktopApi.
 */

import { t, useLocale } from '../i18n/LocaleProvider.tsx'

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
  readAloudDisabled: boolean
  readAloudPendingMessageId: string | null
  readAloudPhase: 'starting' | 'active' | 'stopping' | null
  retryEditDraft: RetryEditDraft | null
  retryPair: RetryableChatPair | null
  retryDisabled: boolean
  onBeginRetryEdit(pair: RetryableChatPair): void
  onCancelRetryEdit(): void
  onCopy(text: string): Promise<void>
  onOpenExternalUrl(url: string): Promise<void>
  onReadAloud(messageId: string): void
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
    labels.push(t('Page {page}', { page: citation.pageNumber }))
  }
  for (const [index, location] of citation.locations.entries()) {
    const locationLabels = [t('Block {block}', { block: location.blockOrdinal + 1 })]
    if (location.kind === 'table') {
      locationLabels.push(
        t('Row {row}, column {column}', { row: location.rowIndex + 1, column: location.columnIndex + 1 }),
      )
    }
    locationLabels.push(
      t('Characters {start}–{end}', { start: location.sourceStartCodePoint, end: location.sourceEndCodePoint }),
    )
    labels.push(
      citation.locations.length > 1
        ? t('Location {index}: {location}', { index: index + 1, location: locationLabels.join(' · ') })
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
  useLocale()
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
        <strong>{t("Project Sources did not produce an answer.")}</strong>
        <span>
          {answer.contextPassageCount === 0
            ? t("No relevant passages were found in the current Project corpus.")
            : t("Relevant passages were found, but they did not support a reliable answer.")}
        </span>
      </div>
    )
  }

  return (
    <div className="grounded-answer">
      <ol className="grounded-statement-list" aria-label={t("Grounded answer statements")}>
        {answer.statements.map((statement) => (
          <li key={statement.statementId} className="grounded-statement">
            <span className={`grounded-kind grounded-kind-${statement.kind}`}>
              {t(statementKindLabel(statement.kind))}
            </span>
            <p>{statement.text}</p>
            <div className="grounded-citation-buttons" aria-label={t("Citations")}>
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
                    {t("[")}{citationNumber}{t("]")} {citation.fileName}
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
          aria-label={t("Citation from {v0}", { v0: activeCitation.fileName })}
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
              aria-label={t("Close citation details")}
              onClick={() => { setActiveCitationId(null) }}
            >
              <Icon name="close" />
            </button>
          </div>
          <p className="grounded-citation-location">
            {t(citationLocationLabel(activeCitation))}
          </p>
          <blockquote>{activeCitation.excerpt}</blockquote>
        </section>
      )}
    </div>
  )
}

interface CopyButtonProps {
  iconOnly?: boolean
  label: string
  text: string
  onCopy(text: string): Promise<void>
}

function CopyButton({
  iconOnly = false,
  label,
  text,
  onCopy,
}: CopyButtonProps) {
  useLocale()
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
    ? t('{label} copied', { label: t(label) })
    : copyState === 'error'
      ? t('{label} failed', { label: t(label) })
      : label
  const titleLabel = copyState === 'copied'
    ? 'Copied'
    : copyState === 'error'
      ? 'Copy failed'
      : label

  return (
    <button
      type="button"
      className="message-action"
      aria-label={t(buttonLabel)}
      title={t(titleLabel)}
      onClick={() => { void copy() }}
    >
      <Icon name={copyState === 'copied' ? 'check' : 'copy'} />
      {!iconOnly && <span>{t(titleLabel)}</span>}
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
  useLocale()
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
                {alt ? t("Image blocked: {v0}", { v0: alt }) : t("Remote image blocked")}
              </span>
            )
          },
          pre({ children }) {
            const code = nodeText(children).replace(/\n$/, '')
            return (
              <div className="code-block">
                <div className="code-block-toolbar">
                  <span>{t("Code")}</span>
                  <CopyButton
                    label={t("Copy code")}
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
          {t("Could not open this link.")}</span>
      )}
    </div>
  )
}

/** Render one user or assistant message with its complete lifecycle state. */
export function MessageView({
  message,
  readAloudDisabled,
  readAloudPendingMessageId,
  readAloudPhase,
  retryEditDraft,
  retryPair,
  retryDisabled,
  onBeginRetryEdit,
  onCancelRetryEdit,
  onCopy,
  onOpenExternalUrl,
  onReadAloud,
  onRetry,
  onRetryEditChange,
}: MessageViewProps) {
  useLocale()
  const editRef = useRef<HTMLTextAreaElement | null>(null)
  const activeEditDraft = retryPair !== null
    && message.role === 'user'
    && message.id === retryPair.userMessageId
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

  const isAssistant = message.role === 'assistant'
  const assistantPairActionsAvailable = (
    isAssistant
    && retryPair !== null
    && message.id === retryPair.assistantMessageId
    && message.state !== 'streaming'
  )
  const userPairActionsAvailable = (
    !isAssistant
    && retryPair !== null
    && message.id === retryPair.userMessageId
  )
  const readAloudPending = readAloudPendingMessageId === message.id
  const messageReadAloudPhase = readAloudPending ? readAloudPhase : null
  const readAloudLabel = messageReadAloudPhase === 'active'
    ? 'Stop reading'
    : messageReadAloudPhase === 'stopping'
      ? 'Stopping read aloud'
      : messageReadAloudPhase === 'starting'
        ? 'Starting read aloud'
        : 'Read aloud'

  return (
    <article
      className={`message ${message.role} message-${message.state}`}
      aria-label={isAssistant ? t("Message from Elysia") : t("Message from you")}
      data-message-id={message.id}
    >
      <div className="message-body">
        <div className="message-surface">
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
                aria-label={t("Attachments in {v0}", { v0: isAssistant ? t('Elysia message') : t('your message') })}
              >
                {message.attachments.map((attachment) => (
                  <li key={attachment.attachmentId}>
                    <Icon name="file" />
                    <span>
                      <strong title={attachment.fileName}>{attachment.fileName}</strong>
                      <small>
                        {attachment.mediaType} {t("·")} {formatAttachmentBytes(attachment.sizeBytes)}
                      </small>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {message.state === 'streaming' && (
            <span className="stream-caret" aria-hidden="true" />
          )}
        </div>

        <div className="message-actions">
          <CopyButton
            iconOnly
            label={t("Copy message")}
            text={message.text}
            onCopy={onCopy}
          />
          {userPairActionsAvailable && activeEditDraft === null && (
            <button
              type="button"
              className="message-action"
              aria-label={t("Edit message")}
              title={t("Edit message")}
              disabled={retryDisabled || anotherEditDraftExists}
              onClick={() => {
                onBeginRetryEdit(retryPair)
              }}
            >
              <Icon name="edit" />
            </button>
          )}
          {isAssistant && (
            <button
              type="button"
              className="message-action"
              aria-label={t(readAloudLabel)}
              title={t(readAloudLabel)}
              aria-pressed={
                messageReadAloudPhase === 'active'
                || messageReadAloudPhase === 'stopping'
              }
              disabled={
                messageReadAloudPhase === 'starting'
                || messageReadAloudPhase === 'stopping'
                || (
                  messageReadAloudPhase === null
                  && (
                    readAloudDisabled
                    || readAloudPendingMessageId !== null
                    || message.state !== 'complete'
                  )
                )
              }
              onClick={() => { onReadAloud(message.id) }}
            >
              <Icon name="speaker" />
            </button>
          )}
          {assistantPairActionsAvailable && (
            <button
              type="button"
              className="message-action"
              aria-label={t("Try again")}
              title={t("Try again")}
              disabled={retryDisabled || anotherEditDraftExists}
              onClick={() => { onRetry(retryPair) }}
            >
              <Icon name="refresh" />
            </button>
          )}
        </div>

        {editing && retryPair !== null && (
          <form
            className="message-edit-form"
            aria-label={t("Edit and retry message")}
            onSubmit={(event) => {
              event.preventDefault()
              if (activeEditDraft.text.trim() === '') {
                return
              }
              onRetry(retryPair, activeEditDraft.text)
            }}
          >
            <label htmlFor={`edit-message-${message.id}`}>
              {t("Edit your last message")}</label>
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
                {t("Cancel")}</button>
              <button
                type="submit"
                className="message-edit-button primary"
                disabled={retryDisabled || activeEditDraft.text.trim() === ''}
              >
                {t("Retry edited message")}</button>
            </div>
          </form>
        )}
      </div>
    </article>
  )
}
