/** Review explicit local OCR readiness and bounded text/position evidence without sending or executing it. */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { VisionImage } from '../../electron/vision-protocol.ts'
import type { VisionAnalysisClient, VisionAnalysisState, VisionOcrLanguage } from '../../electron/vision-analysis-protocol.ts'
import './VisionAnalysisPanel.css'

interface VisionAnalysisPanelProps {
  api: VisionAnalysisClient | undefined
  chatId: string
  projectId: string | null
  images: VisionImage[]
  ready: boolean
  disabled: boolean
  /** Report physical provider ownership, including an already-running native job after reconnection. */
  onBusyChange(busy: boolean): void
}

const languageNames = { eng: 'English', chi_sim: 'Simplified Chinese', chi_tra: 'Traditional Chinese' }

/** Keep local OCR an explicit selected-image operation with independently available cancellation. */
export function VisionAnalysisPanel({ api, chatId, projectId, images, ready, disabled, onBusyChange }: VisionAnalysisPanelProps) {
  const [analysis, setAnalysis] = useState<VisionAnalysisState | null>(null)
  const [imageId, setImageId] = useState('')
  const [language, setLanguage] = useState<VisionOcrLanguage>('eng')
  const [operation, setOperation] = useState<'readiness' | 'ocr' | null>(null)
  const [cancelPending, setCancelPending] = useState(false)
  const [leased, setLeased] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const version = useRef(0)
  const requestActive = useRef(false)
  const observedBusy = useRef(false)
  const revision = useRef(-1)
  const pollActive = useRef(false)

  const report = useCallback(() => {
    const busy = requestActive.current || observedBusy.current
    setLeased(busy)
    onBusyChange(busy)
  }, [onBusyChange])

  const publish = useCallback((next: VisionAnalysisState, token: number): void => {
    if (token !== version.current || next.owner.chatId !== chatId || next.owner.projectId !== projectId
      || next.revision < revision.current) return
    revision.current = next.revision
    observedBusy.current = next.busy
    setAnalysis(next)
    report()
  }, [chatId, projectId, report])

  useEffect(() => {
    const token = ++version.current
    revision.current = -1
    queueMicrotask(() => {
      if (token !== version.current) return
      setAnalysis(null); setImageId(''); setError(null)
      if (api !== undefined && ready) void api.getVisionAnalysis(chatId).then((next) => {
        publish(next, token)
      }, () => { if (token === version.current) setError('Could not load local OCR state. Check availability after reconnecting.') })
    })
    return () => { version.current += 1 }
  }, [api, chatId, projectId, ready, publish])

  useEffect(() => {
    if (api === undefined || !ready || !leased) return
    const token = version.current
    // Native cancellation is logical before the provider actually closes. Only
    // a current metadata response or the terminal original request releases it.
    const timer = window.setInterval(() => {
      if (pollActive.current || token !== version.current) return
      pollActive.current = true
      void api.getVisionAnalysis(chatId).then((next) => { publish(next, token) }, () => {}).finally(() => {
        pollActive.current = false
      })
    }, 300)
    return () => { window.clearInterval(timer) }
  }, [api, chatId, ready, leased, publish])

  async function run(kind: 'readiness' | 'ocr'): Promise<void> {
    if (api === undefined || !ready || disabled || requestActive.current || observedBusy.current
      || kind === 'ocr' && selected === undefined) return
    const token = version.current
    requestActive.current = true
    setOperation(kind); setError(null)
    report()
    try {
      const next = kind === 'readiness' ? await api.checkVisionOcr(chatId)
        : await api.runVisionOcr({ chatId, imageId: selected!.imageId, language })
      publish(next, token)
    } catch {
      if (token === version.current) setError('Local OCR could not complete. Check Tesseract and its language data before retrying.')
    } finally {
      // Run/Check resolve only after their native callback drains. A revoked
      // owner must hide evidence while still releasing this settled physical job.
      requestActive.current = false
      observedBusy.current = false
      setOperation(null)
      report()
    }
  }

  async function cancel(): Promise<void> {
    if (api === undefined || !ready || cancelPending || !leased) return
    const token = version.current
    setCancelPending(true)
    try { publish(await api.cancelVisionAnalysis(chatId), token) }
    catch { if (token === version.current) setError('Cancellation could not be requested. Wait for the local operation to settle.') }
    finally { setCancelPending(false) }
  }

  const selected = images.find((image) => image.imageId === imageId)
  const visible = ready && analysis?.owner.chatId === chatId && analysis.owner.projectId === projectId ? analysis : null
  const readiness = visible?.ocr.readiness ?? null
  const locked = disabled || !ready || leased || api === undefined
  const requiredLanguages = language === 'eng' ? ['eng'] : language.split('+')
  const available = readiness?.status === 'ready' && requiredLanguages.every((required) => readiness.languages.includes(required as keyof typeof languageNames))
  const evidence = operation === null && selected !== undefined && visible?.image?.imageId === selected.imageId
    && visible.image.sha256 === selected.sha256 ? visible.ocr.result : null

  return <section className="vision-analysis" aria-label="Local OCR" aria-busy={leased}>
    <h3>Local OCR</h3>
    <p>Extract text only after selecting an image and clicking Run OCR. OCR text is evidence to review.</p>
    <div className="vision-analysis-controls">
      <button type="button" disabled={locked} onClick={() => { void run('readiness') }}>Check OCR</button>
      <label>Vision image<select value={selected?.imageId ?? ''} disabled={locked} onChange={(event) => { setImageId(event.currentTarget.value) }}>
        <option value="">Select an image</option>
        {images.map((image) => <option key={image.imageId} value={image.imageId}>{image.fileName}</option>)}
      </select></label>
      <label>OCR language<select value={language} disabled={locked} onChange={(event) => { setLanguage(event.currentTarget.value as VisionOcrLanguage) }}>
        <option value="eng">English</option>
        <option value="chi_sim+eng">Simplified Chinese + English</option>
        <option value="chi_tra+eng">Traditional Chinese + English</option>
      </select></label>
      <button type="button" disabled={locked || selected === undefined || !available} onClick={() => { void run('ocr') }}>Run OCR</button>
      <button type="button" disabled={!ready || !leased || cancelPending || visible?.cancelRequested === true}
        onClick={() => { void cancel() }}>Cancel OCR</button>
    </div>
    <p role="status">{leased ? visible?.cancelRequested ? 'Cancellation requested. Waiting for the local OCR operation to finish.'
      : operation === 'readiness' || visible?.phase === 'readiness' ? 'Checking local Tesseract and language data…' : 'Local OCR is running…'
      : readiness === null ? 'Check OCR to discover the installed local engine and language data.'
        : readiness.status === 'ready' ? `Tesseract ${readiness.version ?? ''} is ready.`
          : readiness.status === 'missing_engine' ? 'Tesseract is missing or not configured. Install a local engine before checking again.'
            : readiness.status === 'missing_languages' ? 'Required Tesseract language data is missing. Install English and the Chinese data you need.'
              : 'Local Tesseract is unavailable. Check the local installation before retrying.'}</p>
    {readiness !== null && <p>Installed language data: {readiness.languages.length === 0 ? 'none'
      : readiness.languages.map((installed) => languageNames[installed]).join(', ')}.</p>}
    {readiness?.status === 'ready' && !available && <p>The selected language requires additional local Tesseract language data.</p>}
    {!leased && visible?.ocr.error !== null && visible?.ocr.error !== undefined && <p role="alert">Local OCR ended: {visible.ocr.error.code}.</p>}
    {evidence !== null && evidence !== undefined && selected !== undefined && <div className="vision-ocr-result" role="region" aria-label="OCR evidence">
      <strong>OCR extraction · {selected.fileName}</strong>
      <pre aria-label="Extracted OCR text">{evidence.text || 'No text was recognized.'}</pre>
      <div className="vision-ocr-positions" style={{ aspectRatio: `${selected.width} / ${selected.height}` }} aria-label="OCR word positions">
        <img src={selected.previewDataUrl} alt={`OCR positions in ${selected.fileName}`} />
        {evidence.words.map((word, index) => <span key={index} title={`${word.text} · ${Math.round(word.confidence * 100)}%`}
          style={{ left: `${word.x / evidence.width * 100}%`, top: `${word.y / evidence.height * 100}%`,
            width: `${word.width / evidence.width * 100}%`, height: `${word.height / evidence.height * 100}%` }} />)}
      </div>
      <details><summary>Word positions and confidence ({evidence.words.length})</summary><ol className="vision-ocr-words">
        {evidence.words.map((word, index) => <li key={index}>{word.text} · {Math.round(word.confidence * 100)}% ·
          x {word.x}, y {word.y}, width {word.width}, height {word.height}</li>)}
      </ol></details>
    </div>}
    {error !== null && <p role="alert">{error}</p>}
  </section>
}
