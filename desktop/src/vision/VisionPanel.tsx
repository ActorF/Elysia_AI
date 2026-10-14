/** Coordinate owner-scoped image reviews, explicit local analysis/capture, and physical native action leases. */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { VisionClient, VisionState } from '../../electron/vision-protocol.ts'
import type { VisionAnalysisClient } from '../../electron/vision-analysis-protocol.ts'
import type { VisionCaptureClient } from '../../electron/vision-capture-protocol.ts'
import type { VisionContext, VisionContextClient } from '../../electron/vision-context-protocol.ts'
import type { VisionHistoryClient } from '../../electron/vision-history-protocol.ts'
import { Icon } from '../design-system/Icon.tsx'
import { ToolPanelFrame, ToolTabs, ToolTabPanel } from '../design-system/ToolPanel.tsx'
import { VisionAnalysisPanel } from './VisionAnalysisPanel.tsx'
import { VisionCapturePanel } from './VisionCapturePanel.tsx'
import { VisionContextPanel } from './VisionContextPanel.tsx'
import { VisionHistoryPanel } from './VisionHistoryPanel.tsx'
import './VisionPanel.css'

interface VisionPanelProps {
  api: (VisionClient & VisionAnalysisClient & VisionCaptureClient & VisionContextClient & VisionHistoryClient) | undefined
  chatId: string
  projectId: string | null
  ready: boolean
  disabled: boolean
  /** Append only explicitly resolved text to the current draft, without sending it. */
  onChatDraft?(text: string): void
  /** Select an owned advisory receipt only when the current Chat is already in Work mode. */
  onWorkContext?(context: VisionContext): void
  /** Preserve the shell's physical computer-action lease until a native request settles. */
  onBusyChange(busy: boolean): void
}

function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KiB` : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

/** Display exact selected images shared by Chat and Work without automatically analyzing or sending them. */
export function VisionPanel({ api, chatId, projectId, ready, disabled, onBusyChange, onChatDraft, onWorkContext }: VisionPanelProps) {
  const [state, setState] = useState<VisionState | null>(null)
  const [pending, setPending] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<'analyze' | 'context' | 'history'>('analyze')
  const [captureOpen, setCaptureOpen] = useState(false)
  const [previewErrors, setPreviewErrors] = useState<string[]>([])
  const [analysisBusy, setAnalysisBusy] = useState(false)
  const [captureBusy, setCaptureBusy] = useState(false)
  const [contextBusy, setContextBusy] = useState(false)
  const [historyBusy, setHistoryBusy] = useState(false)
  const [selectedImageId, setSelectedImageId] = useState('')
  const version = useRef(0)
  const physicalPending = useRef(false)
  const analysisLease = useRef(false)
  const captureLease = useRef(false)
  const contextLease = useRef(false)
  const historyLease = useRef(false)
  const reportBusy = useCallback((): void => {
    onBusyChange(physicalPending.current || analysisLease.current || captureLease.current || contextLease.current || historyLease.current)
  }, [onBusyChange])
  const updateAnalysisBusy = useCallback((busy: boolean): void => {
    analysisLease.current = busy
    setAnalysisBusy(busy)
    reportBusy()
  }, [reportBusy])
  const updateCaptureBusy = useCallback((busy: boolean): void => {
    captureLease.current = busy
    setCaptureBusy(busy)
    reportBusy()
  }, [reportBusy])
  const updateContextBusy = useCallback((busy: boolean): void => {
    contextLease.current = busy
    setContextBusy(busy)
    reportBusy()
  }, [reportBusy])
  const updateHistoryBusy = useCallback((busy: boolean): void => {
    historyLease.current = busy
    setHistoryBusy(busy)
    reportBusy()
  }, [reportBusy])

  function owned(next: VisionState): boolean {
    return next.owner.chatId === chatId && next.owner.projectId === projectId
  }

  useEffect(() => {
    const token = ++version.current
    queueMicrotask(() => {
      if (token !== version.current) return
      setState(null); setSelectedImageId(''); setError(null); setPreviewErrors([]); setOpen(false)
      setTab('analyze'); setCaptureOpen(false)
      if (!ready || api === undefined || physicalPending.current) { setLoading(false); return }
      setLoading(true)
      void api.getVisionState(chatId).then((next) => {
        if (token !== version.current) return
        if (next.owner.chatId !== chatId || next.owner.projectId !== projectId) {
          setError('Image state belongs to a different Chat. Reload images after navigation finishes.')
        } else setState(next)
      }, () => {
        if (token === version.current) setError('Could not load selected images. Reload images when the local service is ready.')
      }).finally(() => { if (token === version.current) setLoading(false) })
    })
    return () => { version.current += 1 }
  }, [api, chatId, projectId, ready])

  async function act(operation: (client: VisionClient) => Promise<VisionState>): Promise<void> {
    if (!ready || disabled || api === undefined || physicalPending.current || analysisLease.current || captureLease.current || contextLease.current || historyLease.current) return
    physicalPending.current = true
    const token = ++version.current
    setPending(true); setLoading(false); setError(null); setOpen(true)
    reportBusy()
    try {
      const next = await operation(api)
      if (token !== version.current) return
      if (!owned(next)) throw new Error('Vision owner changed.')
      setState(next); setPreviewErrors([])
      setSelectedImageId((current) => next.items.some((image) => image.imageId === current)
        ? current : next.items.at(-1)?.imageId ?? '')
    } catch {
      if (token === version.current) setError('The image action could not complete. Choose a supported PNG or JPEG, or reload images before retrying.')
    } finally {
      // An owner change or disconnect revokes result publication, but only
      // physical settlement can release the shared native-action capacity.
      physicalPending.current = false
      setPending(false)
      reportBusy()
    }
  }

  const visible = ready && state !== null && owned(state) ? state : null
  const images = visible?.items ?? []
  const selectedImage = images.find((image) => image.imageId === selectedImageId) ?? null
  const locked = disabled || !ready || pending || analysisBusy || captureBusy || contextBusy || historyBusy || visible?.busy === true || loading || api === undefined
  const busy = pending || loading || analysisBusy || captureBusy || contextBusy || historyBusy

  return <ToolPanelFrame id="vision-workbench" title="Vision" description="Images, screenshots and text"
    icon="image" ariaLabel="Vision images" open={open} onToggle={() => { setOpen((current) => !current) }}
    className="vision-panel" busy={busy} status={busy ? 'Working…' : images.length > 0 ? `${images.length} selected` : 'Local images'}
    actions={busy && <button type="button" className="vision-activity" onClick={() => {
      setOpen(true)
      if (captureBusy) setCaptureOpen(true)
      else setTab(contextBusy ? 'context' : historyBusy ? 'history' : 'analyze')
    }}>View active task</button>}>
    <div className="vision-image-review">
      <div className="vision-actions">
        <button type="button" className="tool-primary" disabled={locked || images.length >= 8} onClick={() => {
          void act(async (client) => (await client.chooseVisionImage(chatId)).state)
        }}><Icon name="image" />{pending ? 'Working…' : 'Choose image'}</button>
        {typeof api?.listVisionCaptureSources === 'function' && <button type="button" aria-expanded={captureOpen}
          aria-controls="vision-capture-workspace" onClick={() => { setCaptureOpen((current) => !current) }}>
          <Icon name="monitor" />Take screenshot</button>}
        <details className="vision-image-options"><summary>Image options</summary>
          <p className="vision-session-note">Images stay in this session and clear when the Chat or Project changes.</p>
          <div className="vision-actions">
          <button type="button" disabled={locked} onClick={() => { void act((client) => client.getVisionState(chatId)) }}>Reload images</button>
          <button type="button" disabled={locked || images.length === 0} onClick={() => {
            void act((client) => client.clearVisionState(chatId))
          }}>Clear selected images</button>
        </div></details>
      </div>
      {images.length === 0 && <div className="vision-empty"><Icon name="image" /><div><strong>{loading ? 'Loading selected images…' : 'Start with an image'}</strong>
        <p>PNG, JPEG, or a single screenshot.</p></div></div>}
      {images.length >= 8 && <p>Remove an image before choosing another. Up to eight images can be selected.</p>}
      <div className="vision-images">
        {images.map((image) => <article className="vision-image" key={image.imageId} aria-label={`Selected image ${image.fileName}`}>
          <button type="button" className="vision-image-select" disabled={locked} aria-pressed={image.imageId === selectedImageId}
            aria-label={`Review selected image ${image.fileName}`} onClick={() => { setSelectedImageId(image.imageId) }}>
            {previewErrors.includes(image.imageId) ? <span role="alert">This image preview could not be displayed.</span>
              : <img src={image.previewDataUrl} alt={`Preview of ${image.fileName}`} onError={() => {
                setPreviewErrors((current) => current.includes(image.imageId) ? current : [...current, image.imageId])
              }} />}
            <span className="vision-image-caption"><strong>{image.fileName}</strong>
              <span>{image.width} × {image.height} · {formatBytes(image.sizeBytes)}</span></span>
          </button>
          <button type="button" className="vision-image-remove" disabled={locked} aria-label={`Remove selected image ${image.fileName}`} onClick={() => {
            void act((client) => client.removeVisionImage({ chatId, imageId: image.imageId }))
          }}><Icon name="close" /></button>
        </article>)}
      </div>
      <label className="vision-image-target" hidden={images.length === 0}>Vision image<select value={selectedImage?.imageId ?? ''} disabled={locked}
        onChange={(event) => { setSelectedImageId(event.currentTarget.value) }}>
        <option value="">Select an image</option>
        {images.map((image) => <option key={image.imageId} value={image.imageId}>{image.fileName}</option>)}
      </select></label>
      {error !== null && <p role="alert">{error}</p>}
    </div>
    {/* Presentation never unmounts a native request owner. A collapsed workspace
        or inactive tab must keep cancellation, polling and physical leases alive. */}
    {typeof api?.listVisionCaptureSources === 'function' && <div id="vision-capture-workspace" className="vision-capture-slot" hidden={!captureOpen}>
      <VisionCapturePanel api={api} chatId={chatId} projectId={projectId} ready={ready}
        disabled={disabled || pending || loading || analysisBusy || contextBusy || historyBusy} imageLimitReached={images.length >= 8}
        onBusyChange={updateCaptureBusy} onStateChange={(next) => {
          if (ready && owned(next)) { setState(next); setPreviewErrors([]); setSelectedImageId(next.items.at(-1)?.imageId ?? '') }
        }} />
    </div>}
    <ToolTabs<'analyze' | 'context' | 'history'> idPrefix="vision-workspace" label="Vision tools" value={tab} onValueChange={setTab}
      items={[{ value: 'analyze', label: 'Analyze' }, { value: 'context', label: onWorkContext === undefined ? 'Use in Chat' : 'Use in Work' },
        { value: 'history', label: 'Saved images' }]} />
    <ToolTabPanel idPrefix="vision-workspace" value="analyze" activeValue={tab}>
    {typeof api?.getVisionAnalysis === 'function' && <div className="vision-analysis-slot">
      <VisionAnalysisPanel api={api} chatId={chatId} projectId={projectId} images={images} ready={ready}
        selectedImageId={selectedImageId}
        disabled={disabled || pending || loading || captureBusy || contextBusy || historyBusy} onBusyChange={updateAnalysisBusy} />
    </div>}
    </ToolTabPanel>
    <ToolTabPanel idPrefix="vision-workspace" value="context" activeValue={tab}>
    {typeof api?.prepareVisionContext === 'function' && <div className="vision-analysis-slot">
      <VisionContextPanel api={api} chatId={chatId} projectId={projectId} image={selectedImage} ready={ready}
        disabled={disabled || pending || loading || analysisBusy || captureBusy || historyBusy} onBusyChange={updateContextBusy}
        onChatDraft={onChatDraft} onWorkContext={onWorkContext} />
    </div>}
    </ToolTabPanel>
    <ToolTabPanel idPrefix="vision-workspace" value="history" activeValue={tab}>
    {typeof api?.getVisionHistory === 'function' && <div className="vision-analysis-slot">
      <VisionHistoryPanel api={api} chatId={chatId} projectId={projectId} image={selectedImage} ready={ready}
        disabled={disabled || pending || loading || analysisBusy || captureBusy || contextBusy} onBusyChange={updateHistoryBusy}
        onImagesChange={(next) => {
          if (ready && owned(next)) { setState(next); setPreviewErrors([]); setSelectedImageId(next.items.at(-1)?.imageId ?? '') }
        }} />
    </div>}
    </ToolTabPanel>
  </ToolPanelFrame>
}
