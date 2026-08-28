/**
 * Render canonical Projects and their Chat relationships without owning data.
 *
 * Every callback crosses the DesktopApi boundary in App. This view keeps only
 * drafts and transient interaction state; it never fabricates Project IDs,
 * mutates the returned collections, or treats a native directory choice as a
 * persisted workspace until the canonical callback completes.
 */

import {
  useCallback,
  useId,
  useRef,
  useState,
} from 'react'

import type {
  ArchiveProjectRequest,
  ChatSessionSummary,
  CreateProjectRequest,
  MoveChatToProjectRequest,
  ProjectState,
  ProjectSummary,
  UpdateProjectRequest,
} from '../../electron/contracts.ts'
import {
  codePointLength,
  hasNonBlankCodePoint,
  trimProtocolBlankCharacters,
} from '../../electron/protocol-text.js'
import {
  EmptyState,
  InlineAlert,
  LoadingState,
} from '../design-system/Feedback.tsx'
import { Icon } from '../design-system/Icon.tsx'
import { ChatActionDialog } from '../shell/ChatActionDialog.tsx'
import './ProjectView.css'

const MAX_PROJECT_NAME_LENGTH = 200
const MAX_PROJECT_INSTRUCTIONS_LENGTH = 1_000_000

type ProjectSection = 'chats' | 'sources' | 'memory' | 'settings'

type ProjectDialogState =
  | { kind: 'create' }
  | { kind: 'archive'; project: ProjectSummary }
  | { kind: 'unbind'; project: ProjectSummary }

export interface ProjectViewProps {
  busyChatId?: string
  loading?: boolean
  mutationPending?: boolean
  projectState: ProjectState | null
  sidebarOpen: boolean
  onArchive(request: ArchiveProjectRequest): Promise<void>
  onChooseWorkspace(projectId: string): Promise<boolean>
  onCreate(request: CreateProjectRequest): Promise<void>
  onMoveChat(request: MoveChatToProjectRequest): Promise<void>
  onOpenChat(chatId: string): Promise<void>
  onOpenProject(projectId: string): Promise<void>
  onToggleSidebar(): void
  onUnbindWorkspace(projectId: string): Promise<void>
  onUpdate(request: UpdateProjectRequest): Promise<void>
}

interface ProjectChatRowProps {
  actionDisabled: boolean
  actionLabel: string
  active: boolean
  chat: ChatSessionSummary
  openDisabled: boolean
  onAction(): void
  onOpen(): void
}

interface ProjectSettingsPanelProps {
  pending: boolean
  project: ProjectSummary
  onChooseWorkspace(): void
  onRequestUnbind(): void
  onSave(request: UpdateProjectRequest): void
}

function actionError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : fallback
}

function normalizeInstructions(value: string): string | null {
  const instructions = trimProtocolBlankCharacters(value)
  return hasNonBlankCodePoint(instructions) ? instructions : null
}

function formattedTimestamp(value: string): string {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) {
    return value
  }
  return new Intl.DateTimeFormat('en', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(timestamp)
}

/** Display one Chat using the same server-owned metadata as the Sidebar. */
function ProjectChatRow({
  actionDisabled,
  actionLabel,
  active,
  chat,
  openDisabled,
  onAction,
  onOpen,
}: ProjectChatRowProps) {
  return (
    <li className={'project-chat-row' + (chat.archived ? ' archived' : '')}>
      <div className="project-chat-copy">
        <div className="project-chat-title-line">
          <strong title={chat.title}>{chat.title}</strong>
          {active && <span className="project-chip accent">Current</span>}
          {chat.archived && <span className="project-chip">Archived</span>}
        </div>
        <div className="project-chat-meta">
          <span className={`mode-badge mode-badge-${chat.mode}`}>
            {chat.mode === 'work' ? 'Work' : 'Chat'}
          </span>
          <span>{chat.messageCount} messages</span>
          <span aria-hidden="true">·</span>
          <time dateTime={chat.updatedAt} title={formattedTimestamp(chat.updatedAt)}>
            {formattedTimestamp(chat.updatedAt)}
          </time>
        </div>
      </div>
      <div className="project-chat-actions">
        <button
          type="button"
          className="project-secondary-button"
          disabled={openDisabled}
          aria-label={`Open Chat ${chat.title}`}
          title={chat.archived ? 'Archived chats are read-only' : undefined}
          onClick={onOpen}
        >
          Open
        </button>
        <button
          type="button"
          className="project-secondary-button"
          disabled={actionDisabled}
          title={
            chat.archived
              ? 'Archived chats are read-only'
              : undefined
          }
          aria-label={`${actionLabel} ${chat.title}`}
          onClick={onAction}
        >
          {actionLabel}
        </button>
      </div>
    </li>
  )
}

/** Keep unsaved settings local and reset them only after canonical metadata changes. */
function ProjectSettingsPanel({
  pending,
  project,
  onChooseWorkspace,
  onRequestUnbind,
  onSave,
}: ProjectSettingsPanelProps) {
  const [nameDraft, setNameDraft] = useState(project.name)
  const [instructionsDraft, setInstructionsDraft] = useState(
    project.customInstructions ?? '',
  )
  const settingsHeadingId = useId()
  const nameHelpId = useId()
  const instructionsHelpId = useId()
  const nameLength = codePointLength(nameDraft)
  const instructionsLength = codePointLength(instructionsDraft)
  const normalizedName = trimProtocolBlankCharacters(nameDraft)
  const normalizedInstructions = normalizeInstructions(instructionsDraft)
  const settingsValid = (
    hasNonBlankCodePoint(normalizedName)
    && codePointLength(normalizedName) <= MAX_PROJECT_NAME_LENGTH
    && (
      normalizedInstructions === null
      || codePointLength(normalizedInstructions) <= MAX_PROJECT_INSTRUCTIONS_LENGTH
    )
  )
  const settingsDirty = (
    normalizedName !== project.name
    || normalizedInstructions !== project.customInstructions
  )

  return (
    <section className="project-settings" aria-labelledby={settingsHeadingId}>
      <div className="project-card project-settings-card">
        <div className="project-card-heading">
          <div>
            <h3 id={settingsHeadingId}>Project Settings</h3>
            <p>Name and instructions are shared by every Chat in this Project.</p>
          </div>
        </div>

        <form
          className="project-settings-form"
          onSubmit={(event) => {
            event.preventDefault()
            if (settingsValid && settingsDirty && !project.archived && !pending) {
              onSave({
                projectId: project.projectId,
                name: normalizedName,
                customInstructions: normalizedInstructions,
              })
            }
          }}
        >
          <label className="project-field">
            <span>Project name</span>
            <input
              value={nameDraft}
              disabled={pending || project.archived}
              aria-describedby={nameHelpId}
              aria-invalid={
                !hasNonBlankCodePoint(normalizedName)
                || codePointLength(normalizedName) > MAX_PROJECT_NAME_LENGTH
              }
              onChange={(event) => { setNameDraft(event.target.value) }}
            />
            <small id={nameHelpId}>
              {nameLength.toLocaleString()}/{MAX_PROJECT_NAME_LENGTH} characters
            </small>
          </label>

          <label className="project-field">
            <span>Custom instructions</span>
            <textarea
              value={instructionsDraft}
              rows={8}
              disabled={pending || project.archived}
              aria-describedby={instructionsHelpId}
              aria-invalid={instructionsLength > MAX_PROJECT_INSTRUCTIONS_LENGTH}
              placeholder="How should Elysia work inside this Project?"
              onChange={(event) => { setInstructionsDraft(event.target.value) }}
            />
            <small id={instructionsHelpId}>
              Applied on future turns. Leave blank to clear.
              {' '}{instructionsLength.toLocaleString()}/{MAX_PROJECT_INSTRUCTIONS_LENGTH.toLocaleString()} characters
            </small>
          </label>

          <div className="project-form-actions">
            <button
              type="submit"
              className="project-primary-button"
              disabled={
                pending
                || project.archived
                || !settingsValid
                || !settingsDirty
              }
            >
              Save Settings
            </button>
          </div>
        </form>
      </div>

      <div className="project-card project-workspace-card">
        <div className="project-card-heading">
          <div>
            <h3>Workspace</h3>
            <p>Bind one directory for future Project-aware Work tools.</p>
          </div>
        </div>
        {project.workspacePath === null ? (
          <p className="project-workspace-empty">No workspace is bound.</p>
        ) : (
          <code className="project-workspace-path" title={project.workspacePath}>
            {project.workspacePath}
          </code>
        )}
        <div className="project-form-actions project-workspace-actions">
          <button
            type="button"
            className="project-secondary-button"
            disabled={pending || project.archived}
            onClick={onChooseWorkspace}
          >
            <Icon name="folder" />
            <span>
              {project.workspacePath === null
                ? 'Bind Workspace'
                : 'Replace Workspace'}
            </span>
          </button>
          {project.workspacePath !== null && (
            <button
              type="button"
              className="project-secondary-button danger-action"
              aria-haspopup="dialog"
              disabled={pending || project.archived}
              onClick={onRequestUnbind}
            >
              Unbind Workspace
            </button>
          )}
        </div>
      </div>
    </section>
  )
}

/** Render the complete Project list, detail surfaces, and relationship actions. */
export function ProjectView({
  busyChatId,
  loading = false,
  mutationPending = false,
  projectState,
  sidebarOpen,
  onArchive,
  onChooseWorkspace,
  onCreate,
  onMoveChat,
  onOpenChat,
  onOpenProject,
  onToggleSidebar,
  onUnbindWorkspace,
  onUpdate,
}: ProjectViewProps) {
  const [section, setSection] = useState<ProjectSection>('chats')
  const [dialogState, setDialogState] = useState<ProjectDialogState | null>(null)
  const [createName, setCreateName] = useState('')
  const [createInstructions, setCreateInstructions] = useState('')
  const [localPending, setLocalPending] = useState(false)
  const [openingProjectId, setOpeningProjectId] = useState<string | null>(null)
  const [chatActionId, setChatActionId] = useState<string | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const pendingRef = useRef(false)
  const createButtonRef = useRef<HTMLButtonElement | null>(null)
  const detailActionRef = useRef<HTMLButtonElement | null>(null)
  const detailHeadingRef = useRef<HTMLHeadingElement | null>(null)
  const dialogReturnFocusRef = useRef<HTMLElement | null>(null)

  const activeProject = projectState?.activeProject ?? null
  const projects = projectState?.projects ?? []
  const chats = projectState?.chatState.chats
  const activeChatId = projectState?.chatState.activeChat.chatId
  const pending = mutationPending || localPending
  const projectChats = activeProject === null || chats === undefined
    ? []
    : chats.filter((chat) => chat.projectId === activeProject.projectId)
  const unassignedChats = chats === undefined
    ? []
    : chats.filter((chat) => chat.projectId === null && !chat.archived)

  const restoreDialogFocus = useCallback((dialog: ProjectDialogState): void => {
    window.requestAnimationFrame(() => {
      const returnTarget = dialogReturnFocusRef.current
      dialogReturnFocusRef.current = null
      if (returnTarget !== null && returnTarget.isConnected) {
        returnTarget.focus({ preventScroll: true })
        return
      }
      ;(dialog.kind === 'create'
        ? createButtonRef.current
        : detailActionRef.current
      )?.focus({ preventScroll: true })
    })
  }, [])

  const closeDialog = useCallback((): void => {
    if (mutationPending || pendingRef.current) {
      return
    }
    const closingDialog = dialogState
    setDialogState(null)
    setDialogError(null)
    if (closingDialog !== null) {
      restoreDialogFocus(closingDialog)
    }
  }, [dialogState, mutationPending, restoreDialogFocus])

  function beginAction(status: string): boolean {
    if (mutationPending || pendingRef.current) {
      return false
    }
    pendingRef.current = true
    setLocalPending(true)
    setPageError(null)
    setStatusMessage(status)
    return true
  }

  function finishAction(): void {
    pendingRef.current = false
    setLocalPending(false)
  }

  async function runPageAction(
    pendingStatus: string,
    successStatus: string,
    fallbackError: string,
    action: () => Promise<void>,
  ): Promise<void> {
    if (!beginAction(pendingStatus)) {
      return
    }
    try {
      await action()
      setStatusMessage(successStatus)
    } catch (error) {
      setStatusMessage(null)
      setPageError(actionError(error, fallbackError))
    } finally {
      finishAction()
    }
  }

  function openCreateDialog(): void {
    if (pending) {
      return
    }
    dialogReturnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : createButtonRef.current
    setCreateName('')
    setCreateInstructions('')
    setDialogError(null)
    setDialogState({ kind: 'create' })
  }

  async function confirmDialog(): Promise<void> {
    if (dialogState === null || mutationPending || pendingRef.current) {
      return
    }
    setDialogError(null)

    if (dialogState.kind === 'create') {
      const name = trimProtocolBlankCharacters(createName)
      const customInstructions = normalizeInstructions(createInstructions)
      if (!hasNonBlankCodePoint(name)) {
        setDialogError('Enter a name for this Project.')
        return
      }
      if (codePointLength(name) > MAX_PROJECT_NAME_LENGTH) {
        setDialogError(`Project names can use at most ${MAX_PROJECT_NAME_LENGTH} characters.`)
        return
      }
      if (
        customInstructions !== null
        && codePointLength(customInstructions) > MAX_PROJECT_INSTRUCTIONS_LENGTH
      ) {
        setDialogError('Project instructions are too long to save.')
        return
      }

      pendingRef.current = true
      setLocalPending(true)
      try {
        await onCreate({ name, customInstructions })
        setDialogState(null)
        setStatusMessage(`Created Project ${name}.`)
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            detailHeadingRef.current?.focus({ preventScroll: true })
          })
        })
      } catch (error) {
        setDialogError(actionError(error, 'Could not create the Project.'))
      } finally {
        finishAction()
      }
      return
    }

    pendingRef.current = true
    setLocalPending(true)
    try {
      if (dialogState.kind === 'archive') {
        await onArchive({
          projectId: dialogState.project.projectId,
          archived: true,
        })
        setStatusMessage(`Archived Project ${dialogState.project.name}.`)
      } else {
        await onUnbindWorkspace(dialogState.project.projectId)
        setStatusMessage(`Unbound the workspace from ${dialogState.project.name}.`)
      }
      const completedDialog = dialogState
      setDialogState(null)
      window.requestAnimationFrame(() => {
        detailActionRef.current?.focus({ preventScroll: true })
      })
      setDialogError(null)
      if (completedDialog.kind === 'archive') {
        setSection('chats')
      }
    } catch (error) {
      setDialogError(actionError(
        error,
        dialogState.kind === 'archive'
          ? 'Could not archive the Project.'
          : 'Could not unbind the workspace.',
      ))
    } finally {
      finishAction()
    }
  }

  async function openProject(projectId: string): Promise<void> {
    if (
      pending
      || openingProjectId !== null
      || activeProject?.projectId === projectId
    ) {
      return
    }
    setSection('chats')
    setPageError(null)
    setStatusMessage(null)
    setOpeningProjectId(projectId)
    await runPageAction(
      'Opening Project…',
      'Project opened.',
      'Could not open the Project.',
      () => onOpenProject(projectId),
    )
    setOpeningProjectId(null)
    window.requestAnimationFrame(() => {
      detailHeadingRef.current?.focus({ preventScroll: true })
    })
  }

  async function saveSettings(request: UpdateProjectRequest): Promise<void> {
    await runPageAction(
      'Saving Project settings…',
      'Project settings saved.',
      'Could not save the Project settings.',
      () => onUpdate(request),
    )
  }

  async function chooseWorkspace(): Promise<void> {
    if (activeProject === null || activeProject.archived) {
      return
    }
    if (!beginAction('Choosing a workspace…')) {
      return
    }
    try {
      const changed = await onChooseWorkspace(activeProject.projectId)
      if (!changed) {
        setStatusMessage('Workspace selection canceled. Nothing changed.')
        return
      }
      setStatusMessage(
        activeProject.workspacePath === null
          ? 'Workspace bound to the Project.'
          : 'Project workspace replaced.',
      )
    } catch (error) {
      setStatusMessage(null)
      setPageError(actionError(error, 'Could not bind the workspace.'))
    } finally {
      finishAction()
    }
  }

  async function restoreProject(project: ProjectSummary): Promise<void> {
    await runPageAction(
      'Restoring Project…',
      `Restored Project ${project.name}.`,
      'Could not restore the Project.',
      () => onArchive({ projectId: project.projectId, archived: false }),
    )
  }

  async function moveChat(
    chat: ChatSessionSummary,
    projectId: string | null,
  ): Promise<void> {
    if (chatActionId !== null) {
      return
    }
    setChatActionId(chat.chatId)
    await runPageAction(
      projectId === null ? 'Removing Chat from Project…' : 'Moving Chat into Project…',
      projectId === null ? 'Chat removed from the Project.' : 'Chat moved into the Project.',
      'Could not update the Chat Project.',
      () => onMoveChat({ chatId: chat.chatId, projectId }),
    )
    setChatActionId(null)
  }

  async function openChat(chat: ChatSessionSummary): Promise<void> {
    if (chatActionId !== null) {
      return
    }
    setChatActionId(chat.chatId)
    await runPageAction(
      'Opening Chat…',
      'Chat opened.',
      'Could not open the Chat.',
      () => onOpenChat(chat.chatId),
    )
    setChatActionId(null)
  }

  let dialogTitle = ''
  let dialogDescription = null
  let dialogConfirmLabel = ''
  if (dialogState?.kind === 'create') {
    dialogTitle = 'Create Project'
    dialogDescription = 'Give the Project a stable name and optional shared instructions.'
    dialogConfirmLabel = 'Create Project'
  } else if (dialogState?.kind === 'archive') {
    dialogTitle = `Archive “${dialogState.project.name}”?`
    dialogDescription = (
      <p>
        The Project and its Chats stay stored, but the Project becomes read-only
        until it is restored.
      </p>
    )
    dialogConfirmLabel = 'Archive Project'
  } else if (dialogState?.kind === 'unbind') {
    dialogTitle = `Unbind workspace from “${dialogState.project.name}”?`
    dialogDescription = (
      <p>
        This removes only the Project association. Files in the directory are
        not changed or deleted.
      </p>
    )
    dialogConfirmLabel = 'Unbind Workspace'
  }

  return (
    <div className="project-view">
      <header className="topbar page-topbar project-topbar">
        <div className="topbar-leading">
          <button
            type="button"
            className="icon-button sidebar-toggle"
            aria-label={sidebarOpen ? 'Hide navigation' : 'Show navigation'}
            aria-controls="app-sidebar"
            aria-expanded={sidebarOpen}
            title="Toggle navigation (Ctrl+B)"
            onClick={onToggleSidebar}
          >
            <Icon name="menu" />
          </button>
          <div className="project-page-heading">
            <h1>Projects</h1>
            <span>Shared context, Chats, and workspaces</span>
          </div>
        </div>
        <button
          ref={createButtonRef}
          type="button"
          className="project-primary-button"
          aria-haspopup="dialog"
          disabled={pending || loading}
          onClick={openCreateDialog}
        >
          <Icon name="plus" />
          <span>New Project</span>
        </button>
      </header>

      <main className="project-content" aria-busy={loading || pending}>
        {(pageError !== null || statusMessage !== null) && (
          <div className="project-page-feedback">
            {pageError !== null && (
              <InlineAlert
                tone="error"
                title="Project action failed"
                dismissLabel="Dismiss Project error"
                onDismiss={() => { setPageError(null) }}
              >
                {pageError}
              </InlineAlert>
            )}
            {statusMessage !== null && pageError === null && (
              <p className="project-operation-status" role="status" aria-live="polite">
                {statusMessage}
              </p>
            )}
          </div>
        )}

        {loading || projectState === null ? (
          <LoadingState
            className="project-loading"
            title="Loading Projects"
            description="Reading canonical Project and Chat relationships from the local Backend."
          />
        ) : projects.length === 0 ? (
          <EmptyState
            className="project-empty"
            icon="folder"
            title="No Projects yet"
            description="Create a Project to share instructions and a workspace across multiple Chats."
            action={{
              label: 'Create Project',
              disabled: pending,
              onClick: openCreateDialog,
            }}
          />
        ) : (
          <div className="project-split-view">
            <nav className="project-list-panel" aria-label="Projects">
              <div className="project-list-heading">
                <strong>All Projects</strong>
                <span>{projects.length}</span>
              </div>
              <ul className="project-list">
                {projects.map((project) => {
                  const selected = project.projectId === activeProject?.projectId
                  return (
                    <li key={project.projectId}>
                      <button
                        type="button"
                        className={'project-list-button' + (selected ? ' active' : '')}
                        aria-current={selected ? 'page' : undefined}
                        aria-label={`Open project ${project.name}`}
                        disabled={pending || openingProjectId !== null}
                        onClick={() => { void openProject(project.projectId) }}
                      >
                        <span className="project-list-icon"><Icon name="folder" /></span>
                        <span className="project-list-copy">
                          <span className="project-list-title-line">
                            <strong title={project.name}>{project.name}</strong>
                            {project.archived && <span className="project-chip">Archived</span>}
                          </span>
                          <span className="project-list-meta">
                            {project.chatCount} {project.chatCount === 1 ? 'Chat' : 'Chats'}
                            <span aria-hidden="true">·</span>
                            {project.workspacePath === null ? 'No workspace' : 'Workspace bound'}
                          </span>
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </nav>

            {activeProject === null ? (
              <section className="project-detail project-detail-empty">
                <EmptyState
                  icon="folder"
                  title="Choose a Project"
                  description="Select a Project from the list to see its Chats and shared context."
                />
              </section>
            ) : (
              <article className="project-detail" aria-labelledby="active-project-heading">
                <header className="project-detail-header">
                  <div className="project-detail-title">
                    <span className="eyebrow">
                      {activeProject.archived ? 'Archived Project' : 'Active Project'}
                    </span>
                    <h2
                      ref={detailHeadingRef}
                      id="active-project-heading"
                      tabIndex={-1}
                      title={activeProject.name}
                    >
                      {activeProject.name}
                    </h2>
                    <p>
                      {activeProject.chatCount} shared {activeProject.chatCount === 1 ? 'Chat' : 'Chats'}
                      <span aria-hidden="true"> · </span>
                      Updated {formattedTimestamp(activeProject.updatedAt)}
                    </p>
                  </div>
                  {activeProject.archived ? (
                    <button
                      ref={detailActionRef}
                      type="button"
                      className="project-secondary-button"
                      disabled={pending}
                      onClick={() => { void restoreProject(activeProject) }}
                    >
                      <Icon name="archive" />
                      <span>Restore Project</span>
                    </button>
                  ) : (
                    <button
                      ref={detailActionRef}
                      type="button"
                      className="project-secondary-button danger-action"
                      aria-haspopup="dialog"
                      disabled={pending}
                      onClick={() => {
                        dialogReturnFocusRef.current = document.activeElement instanceof HTMLElement
                          ? document.activeElement
                          : detailActionRef.current
                        setDialogError(null)
                        setDialogState({ kind: 'archive', project: activeProject })
                      }}
                    >
                      <Icon name="archive" />
                      <span>Archive Project</span>
                    </button>
                  )}
                </header>

                {activeProject.archived && (
                  <InlineAlert tone="warning" title="Read-only Project">
                    Restore this Project before editing settings, changing its workspace,
                    or moving Chats.
                  </InlineAlert>
                )}

                <nav className="project-section-nav" aria-label="Project sections">
                  <button
                    type="button"
                    aria-current={section === 'chats' ? 'page' : undefined}
                    onClick={() => { setSection('chats') }}
                  >
                    <Icon name="chat" /><span>Chats</span>
                  </button>
                  <button
                    type="button"
                    aria-current={section === 'sources' ? 'page' : undefined}
                    onClick={() => { setSection('sources') }}
                  >
                    <Icon name="file" /><span>Sources</span>
                  </button>
                  <button
                    type="button"
                    aria-current={section === 'memory' ? 'page' : undefined}
                    onClick={() => { setSection('memory') }}
                  >
                    <Icon name="memory" /><span>Memory</span>
                  </button>
                  <button
                    type="button"
                    aria-current={section === 'settings' ? 'page' : undefined}
                    onClick={() => { setSection('settings') }}
                  >
                    <Icon name="settings" /><span>Settings</span>
                  </button>
                </nav>

                <div className="project-detail-scroll">
                  {section === 'chats' && (
                    <div className="project-chat-columns">
                      <section className="project-card" aria-labelledby="project-chats-heading">
                        <div className="project-card-heading">
                          <div>
                            <h3 id="project-chats-heading">Project Chats</h3>
                            <p>Chats that share this Project's context.</p>
                          </div>
                          <span className="project-count">{projectChats.length}</span>
                        </div>
                        {projectChats.length === 0 ? (
                          <EmptyState
                            className="project-card-empty"
                            icon="chat"
                            title="No Project Chats"
                            description="Move an unassigned Chat into this Project to start sharing context."
                          />
                        ) : (
                          <ul className="project-chat-list">
                            {projectChats.map((chat) => {
                              const chatBusy = chat.chatId === busyChatId
                              return (
                                <ProjectChatRow
                                  key={chat.chatId}
                                  active={chat.chatId === activeChatId}
                                  chat={chat}
                                  actionLabel="Remove"
                                  actionDisabled={
                                    pending
                                    || activeProject.archived
                                    || chat.archived
                                    || chatBusy
                                  }
                                  openDisabled={
                                    pending
                                    || activeProject.archived
                                    || chat.archived
                                    || busyChatId !== undefined
                                  }
                                  onAction={() => { void moveChat(chat, null) }}
                                  onOpen={() => { void openChat(chat) }}
                                />
                              )
                            })}
                          </ul>
                        )}
                      </section>

                      <section className="project-card" aria-labelledby="unassigned-chats-heading">
                        <div className="project-card-heading">
                          <div>
                            <h3 id="unassigned-chats-heading">Unassigned Chats</h3>
                            <p>Move a Chat here without changing its messages.</p>
                          </div>
                          <span className="project-count">{unassignedChats.length}</span>
                        </div>
                        {unassignedChats.length === 0 ? (
                          <EmptyState
                            className="project-card-empty"
                            icon="chat"
                            title="No Unassigned Chats"
                            description="Every active Chat already belongs to a Project."
                          />
                        ) : (
                          <ul className="project-chat-list">
                            {unassignedChats.map((chat) => {
                              const chatBusy = chat.chatId === busyChatId
                              return (
                                <ProjectChatRow
                                  key={chat.chatId}
                                  active={chat.chatId === activeChatId}
                                  chat={chat}
                                  actionLabel="Move here"
                                  actionDisabled={pending || activeProject.archived || chatBusy}
                                  openDisabled={pending || busyChatId !== undefined}
                                  onAction={() => {
                                    void moveChat(chat, activeProject.projectId)
                                  }}
                                  onOpen={() => { void openChat(chat) }}
                                />
                              )
                            })}
                          </ul>
                        )}
                      </section>
                    </div>
                  )}

                  {section === 'sources' && (
                    <section className="project-card project-feature-placeholder">
                      <EmptyState
                        icon="file"
                        title="Project Sources aren't connected yet"
                        description="This entry is reserved for shared Project files and indexing. Sources remain untouched until their local service is connected."
                      />
                    </section>
                  )}

                  {section === 'memory' && (
                    <section className="project-card project-feature-placeholder">
                      <EmptyState
                        icon="memory"
                        title="Project Memory isn't connected yet"
                        description="This entry will show only memory scoped to this Project. No global or other-Project memory is exposed here."
                      />
                    </section>
                  )}

                  {section === 'settings' && (
                    <ProjectSettingsPanel
                      key={`${activeProject.projectId}:${activeProject.updatedAt}`}
                      pending={pending}
                      project={activeProject}
                      onChooseWorkspace={() => { void chooseWorkspace() }}
                      onRequestUnbind={() => {
                        dialogReturnFocusRef.current = document.activeElement instanceof HTMLElement
                          ? document.activeElement
                          : null
                        setDialogError(null)
                        setDialogState({ kind: 'unbind', project: activeProject })
                      }}
                      onSave={(request) => { void saveSettings(request) }}
                    />
                  )}
                </div>
              </article>
            )}
          </div>
        )}
      </main>

      <ChatActionDialog
        open={dialogState !== null}
        title={dialogTitle}
        description={dialogDescription}
        confirmLabel={dialogConfirmLabel}
        danger={dialogState?.kind === 'archive' || dialogState?.kind === 'unbind'}
        pending={pending}
        error={dialogError}
        onCancel={closeDialog}
        onConfirm={() => { void confirmDialog() }}
      >
        {dialogState?.kind === 'create' ? (
          <div className="project-dialog-fields">
            <label className="project-field">
              <span>Project name</span>
              <input
                data-dialog-initial-focus
                value={createName}
                disabled={pending}
                aria-invalid={
                  !hasNonBlankCodePoint(createName)
                  || codePointLength(trimProtocolBlankCharacters(createName)) > MAX_PROJECT_NAME_LENGTH
                }
                onChange={(event) => { setCreateName(event.target.value) }}
              />
              <small>
                {codePointLength(createName).toLocaleString()}/{MAX_PROJECT_NAME_LENGTH} characters
              </small>
            </label>
            <label className="project-field">
              <span>Custom instructions <span className="project-optional">Optional</span></span>
              <textarea
                value={createInstructions}
                rows={5}
                disabled={pending}
                placeholder="Shared guidance for Chats in this Project"
                aria-invalid={
                  codePointLength(createInstructions) > MAX_PROJECT_INSTRUCTIONS_LENGTH
                }
                onChange={(event) => { setCreateInstructions(event.target.value) }}
              />
              <small>
                {codePointLength(createInstructions).toLocaleString()}/{MAX_PROJECT_INSTRUCTIONS_LENGTH.toLocaleString()} characters
              </small>
            </label>
          </div>
        ) : undefined}
      </ChatActionDialog>
    </div>
  )
}
