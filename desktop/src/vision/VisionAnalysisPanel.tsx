/** Review explicit local OCR and vision-model readiness with separate bounded evidence, without sending or executing it. */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { VisionImage } from '../../electron/vision-protocol.ts'
import type { VisionAnalysisClient, VisionAnalysisState, VisionOcrLanguage } from '../../electron/vision-analysis-protocol.ts'
import './VisionAnalysisPanel.css'

interface VisionAnalysisPanelProps {
  api: VisionAnalysisClient | undefined
  chatId: string
  projectId: string | null
  images: VisionImage[]
  selectedImageId: string
  /** Change the shared review target without starting a provider or granting any native action. */
  onSelectImage(imageId: string): void
  ready: boolean
  disabled: boolean
  /** Report physical provider ownership, including an already-running native job after reconnection. */
  onBusyChange(busy: boolean): void
}

const languageNames = { eng: 'English', chi_sim: 'Simplified Chinese', chi_tra: 'Traditional Chinese' }

/** Analyze one selected image through explicit local providers and keep cancellation independent of their physical drain. */
export function VisionAnalysisPanel({ api, chatId, projectId, images, selectedImageId, onSelectImage, ready, disabled, onBusyChange }: VisionAnalysisPanelProps) {
  const [analysis, setAnalysis] = useState<VisionAnalysisState | null>(null)
  const [language, setLanguage] = useState<VisionOcrLanguage>('eng')
  const [operation, setOperation] = useState<'readiness' | 'ocr' | 'model-readiness' | 'model' | null>(null)
  const [modelName, setModelName] = useState('')
  const [question, setQuestion] = useState('Describe this image.')
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
      setAnalysis(null); setError(null)
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

  async function run(kind: 'readiness' | 'ocr' | 'model-readiness' | 'model'): Promise<void> {
    if (api === undefined || !ready || disabled || requestActive.current || observedBusy.current
      || kind === 'ocr' && (selected === undefined || !available)
      || kind === 'model' && (selected === undefined || !modelAvailable || !questionValid)) return
    const token = version.current
    requestActive.current = true
    setOperation(kind); setError(null)
    report()
    try {
      const next = kind === 'readiness' ? await api.checkVisionOcr(chatId)
        : kind === 'model-readiness' ? await api.checkVisionModel(chatId)
          : kind === 'model' ? await api.askVisionImage({ chatId, imageId: selected!.imageId, model: selectedModel, question })
            : await api.runVisionOcr({ chatId, imageId: selected!.imageId, language })
      publish(next, token)
    } catch {
      if (token === version.current) setError('Local analysis could not complete. Check the local provider before retrying.')
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

  const selected = images.find((image) => image.imageId === selectedImageId)
  const visible = ready && analysis?.owner.chatId === chatId && analysis.owner.projectId === projectId ? analysis : null
  const readiness = visible?.ocr.readiness ?? null
  const modelReadiness = visible?.model.readiness ?? null
  const selectedModel = modelReadiness?.models.includes(modelName) ? modelName : modelReadiness?.defaultModel ?? ''
  const modelAvailable = modelReadiness?.status === 'ready' && modelReadiness.models.includes(selectedModel)
  const questionBytes = new TextEncoder().encode(question).length
  const questionValid = question.trim() !== '' && questionBytes <= 4096
  const locked = disabled || !ready || leased || api === undefined
  const requiredLanguages = language === 'eng' ? ['eng'] : language.split('+')
  const available = readiness?.status === 'ready' && requiredLanguages.every((required) => readiness.languages.includes(required as keyof typeof languageNames))
  const evidence = operation === null && selected !== undefined && visible?.image?.imageId === selected.imageId
    && visible.image.sha256 === selected.sha256 ? visible.ocr.result : null
  const inference = operation === null && selected !== undefined && visible?.image?.imageId === selected.imageId
    && visible.image.sha256 === selected.sha256 ? visible.model.result : null
  const phase = operation ?? visible?.phase

  return <section className="vision-analysis" aria-label="Visual analysis" aria-busy={leased}>
    <div className="vision-analysis-controls">
      <label>Vision image<select value={selected?.imageId ?? ''} disabled={locked} onChange={(event) => { onSelectImage(event.currentTarget.value) }}>
        <option value="">Select an image</option>
        {images.map((image) => <option key={image.imageId} value={image.imageId}>{image.fileName}</option>)}
      </select></label>
      <button type="button" disabled={!ready || !leased || cancelPending || visible?.cancelRequested === true}
        onClick={() => { void cancel() }}>Cancel analysis</button>
    </div>
    {leased && <p role="status">{visible?.cancelRequested ? 'Cancellation requested. Waiting for the local analysis operation to finish.'
      : phase === 'model-readiness' ? 'Checking installed local vision models…'
        : phase === 'model' ? 'The local vision model is analyzing the selected image…'
          : phase === 'readiness' ? 'Checking local Tesseract and language data…' : 'Local OCR is running…'}</p>}
    <section className="vision-provider" aria-label="Local OCR">
      <h3>Local OCR</h3>
      <p>Extract text only after selecting an image and clicking Run OCR. OCR text is evidence to review.</p>
      <div className="vision-analysis-controls">
      <button type="button" disabled={locked} onClick={() => { void run('readiness') }}>Check OCR</button>
      <label>OCR language<select value={language} disabled={locked} onChange={(event) => { setLanguage(event.currentTarget.value as VisionOcrLanguage) }}>
        <option value="eng">English</option>
        <option value="chi_sim+eng">Simplified Chinese + English</option>
        <option value="chi_tra+eng">Traditional Chinese + English</option>
      </select></label>
      <button type="button" disabled={locked || selected === undefined || !available} onClick={() => { void run('ocr') }}>Run OCR</button>
      </div>
    <p role="status">{readiness === null ? 'Check OCR to discover the installed local engine and language data.'
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
      {/* Bound both dimensions while preserving the original aspect ratio; a
          valid narrow/tall image must not produce a million-pixel review row. */}
      <div className="vision-ocr-positions" style={{ aspectRatio: `${selected.width} / ${selected.height}`,
        width: `min(100%, ${Math.min(20, 12 * selected.width / selected.height)}rem)` }} aria-label="OCR word positions">
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
    </section>
    <section className="vision-provider" aria-label="Local vision model">
      <h3>Local vision model</h3>
      <p>Ask about the selected image using an installed local model. Model inference can be wrong.</p>
      <div className="vision-analysis-controls">
        <button type="button" disabled={locked} onClick={() => { void run('model-readiness') }}>Check vision models</button>
        <label>Vision model<select value={selectedModel} disabled={locked} onChange={(event) => { setModelName(event.currentTarget.value) }}>
          <option value="">Select a vision model</option>
          {(modelReadiness?.models ?? []).map((model) => <option key={model} value={model}>{model}</option>)}
        </select></label>
      </div>
      <p role="status">{modelReadiness === null ? 'Check which installed local models advertise vision support.'
        : modelReadiness.status === 'ready' ? `${modelReadiness.models.length} installed vision model${modelReadiness.models.length === 1 ? '' : 's'} available.`
          : modelReadiness.status === 'no_vision_model' ? 'No installed model advertises vision support. Configure a local Ollama vision model and check again.'
            : 'Local vision models are unavailable. Start local Ollama and check again.'}</p>
      <label className="vision-model-question">Question about image<textarea rows={3} maxLength={4096} value={question}
        disabled={locked} onChange={(event) => { setQuestion(event.currentTarget.value) }} /></label>
      <p>{questionBytes} / 4096 UTF-8 bytes.</p>
      {!questionValid && <p>Enter a question within the byte limit before asking.</p>}
      <button type="button" disabled={locked || selected === undefined || !modelAvailable || !questionValid}
        onClick={() => { void run('model') }}>Ask about image</button>
      {!leased && visible?.model.error !== null && visible?.model.error !== undefined && <p role="alert">Local vision model ended: {visible.model.error.code}.</p>}
      {inference !== null && inference !== undefined && <div className="vision-ocr-result" role="region" aria-label="Model inference">
        <strong>Model inference · {inference.model}</strong>
        <p>Question: {inference.question}</p>
        <pre aria-label="Local model inference">{inference.text || 'The local model returned no description.'}</pre>
        <p>Review this interpretation against the selected image and OCR evidence.</p>
        {inference.truncated && <p role="alert">The model response was truncated. Review it as incomplete.</p>}
      </div>}
    </section>
    {error !== null && <p role="alert">{error}</p>}
  </section>
}
