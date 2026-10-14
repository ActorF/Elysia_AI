/** Coordinate owner-scoped image reviews, explicit local analysis/capture, and physical native action leases. */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { VisionClient, VisionState } from '../../electron/vision-protocol.ts'
import type { VisionAnalysisClient } from '../../electron/vision-analysis-protocol.ts'
import type { VisionCaptureClient } from '../../electron/vision-capture-protocol.ts'
import type { VisionContextClient } from '../../electron/vision-context-protocol.ts'
import { VisionAnalysisPanel } from './VisionAnalysisPanel.tsx'
import { VisionCapturePanel } from './VisionCapturePanel.tsx'
import { VisionContextPanel } from './VisionContextPanel.tsx'
import './VisionPanel.css'

interface VisionPanelProps {
  api: (VisionClient & VisionAnalysisClient & VisionCaptureClient & VisionContextClient) | undefined
  chatId: string
  projectId: string | null
  ready: boolean
  disabled: boolean
  /** Preserve the shell's physical computer-action lease until a native request settles. */
  onBusyChange(busy: boolean): void
}

function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KiB` : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

/** Display exact selected images shared by Chat and Work without automatically analyzing or sending them. */
export function VisionPanel({ api, chatId, projectId, ready, disabled, onBusyChange }: VisionPanelProps) {
  const [state, setState] = useState<VisionState | null>(null)
  const [pending, setPending] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [previewErrors, setPreviewErrors] = useState<string[]>([])
  const [analysisBusy, setAnalysisBusy] = useState(false)
  const [captureBusy, setCaptureBusy] = useState(false)
  const [contextBusy, setContextBusy] = useState(false)
  const [selectedImageId, setSelectedImageId] = useState('')
  const version = useRef(0)
  const physicalPending = useRef(false)
  const analysisLease = useRef(false)
  const captureLease = useRef(false)
  const contextLease = useRef(false)
  const reportBusy = useCallback((): void => {
    onBusyChange(physicalPending.current || analysisLease.current || captureLease.current || contextLease.current)
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

  function owned(next: VisionState): boolean {
    return next.owner.chatId === chatId && next.owner.projectId === projectId
  }

  useEffect(() => {
    const token = ++version.current
    queueMicrotask(() => {
      if (token !== version.current) return
      setState(null); setSelectedImageId(''); setError(null); setPreviewErrors([]); setOpen(false)
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
    if (!ready || disabled || api === undefined || physicalPending.current || analysisLease.current || captureLease.current || contextLease.current) return
    physicalPending.current = true
    const token = ++version.current
    setPending(true); setLoading(false); setError(null); setOpen(true)
    reportBusy()
    try {
      const next = await operation(api)
      if (token !== version.current) return
      if (!owned(next)) throw new Error('Vision owner changed.')
      setState(next); setPreviewErrors([])
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
  const locked = disabled || !ready || pending || analysisBusy || captureBusy || contextBusy || visible?.busy === true || loading || api === undefined

  return <section className="vision-panel" aria-label="Vision images" aria-busy={pending || loading || analysisBusy || captureBusy || contextBusy}>
    <button type="button" className="vision-toggle" aria-expanded={open} aria-controls="vision-image-review"
      onClick={() => { setOpen((current) => !current) }}>
      Vision{images.length > 0 ? ` · ${images.length} selected` : ''}
    </button>
    {open && <div id="vision-image-review" className="vision-image-review">
      <p>Choose a PNG or JPEG to review in this Chat. Images stay in this session and clear when the Chat or Project changes.</p>
      <div className="vision-actions">
        <button type="button" disabled={locked || images.length >= 8} onClick={() => {
          void act(async (client) => (await client.chooseVisionImage(chatId)).state)
        }}>{pending ? 'Working…' : 'Choose image'}</button>
        <button type="button" disabled={locked} onClick={() => { void act((client) => client.getVisionState(chatId)) }}>Reload images</button>
        <button type="button" disabled={locked || images.length === 0} onClick={() => {
          void act((client) => client.clearVisionState(chatId))
        }}>Clear selected images</button>
      </div>
      {images.length === 0 && <p>{loading ? 'Loading selected images…' : 'No images selected.'}</p>}
      {images.length >= 8 && <p>Remove an image before choosing another. Up to eight images can be selected.</p>}
      <div className="vision-images">
        {images.map((image) => <article className="vision-image" key={image.imageId} aria-label={`Selected image ${image.fileName}`}>
          {previewErrors.includes(image.imageId) ? <p role="alert">This image preview could not be displayed.</p>
            : <img src={image.previewDataUrl} alt={`Preview of ${image.fileName}`} onError={() => {
              setPreviewErrors((current) => current.includes(image.imageId) ? current : [...current, image.imageId])
            }} />}
          <strong>{image.fileName}</strong>
          <span>{image.width} × {image.height} · {formatBytes(image.sizeBytes)}</span>
          <button type="button" disabled={locked} aria-label={`Remove selected image ${image.fileName}`} onClick={() => {
            void act((client) => client.removeVisionImage({ chatId, imageId: image.imageId }))
          }}>Remove image</button>
        </article>)}
      </div>
      {error !== null && <p role="alert">{error}</p>}
    </div>}
    {typeof api?.listVisionCaptureSources === 'function' && <div className="vision-analysis-slot" hidden={!open}>
      <VisionCapturePanel api={api} chatId={chatId} projectId={projectId} ready={ready}
        disabled={disabled || pending || loading || analysisBusy || contextBusy} imageLimitReached={images.length >= 8}
        onBusyChange={updateCaptureBusy} onStateChange={(next) => {
          if (ready && owned(next)) { setState(next); setPreviewErrors([]) }
        }} />
    </div>}
    {typeof api?.getVisionAnalysis === 'function' && <div className="vision-analysis-slot" hidden={!open}>
      {/* Keep the analysis owner mounted while the review is collapsed so a
          pending provider retains cancellation/polling and its physical lease. */}
      <VisionAnalysisPanel api={api} chatId={chatId} projectId={projectId} images={images} ready={ready}
        selectedImageId={selectedImageId} onSelectImage={setSelectedImageId}
        disabled={disabled || pending || loading || captureBusy || contextBusy} onBusyChange={updateAnalysisBusy} />
    </div>}
    {typeof api?.prepareVisionContext === 'function' && <div className="vision-analysis-slot" hidden={!open}>
      <VisionContextPanel api={api} chatId={chatId} projectId={projectId} image={selectedImage} ready={ready}
        disabled={disabled || pending || loading || analysisBusy || captureBusy} onBusyChange={updateContextBusy} />
    </div>}
  </section>
}
