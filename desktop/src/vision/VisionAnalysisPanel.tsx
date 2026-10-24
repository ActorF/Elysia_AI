/** Review explicit local OCR and vision-model readiness with separate bounded evidence, without sending or executing it. */

import { t, useTranslation } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { VisionImage } from '../../electron/vision-protocol.ts'
import type { VisionAnalysisClient, VisionAnalysisState, VisionOcrLanguage } from '../../electron/vision-analysis-protocol.ts'
import { ToolTabs, ToolTabPanel } from '../design-system/ToolPanel.tsx'
import { HelpHint } from '../design-system/HelpHint.tsx'
import './VisionAnalysisPanel.css'

interface VisionAnalysisPanelProps {
  api: VisionAnalysisClient | undefined
  chatId: string
  projectId: string | null
  images: VisionImage[]
  selectedImageId: string
  ready: boolean
  disabled: boolean
  /** Report physical provider ownership, including an already-running native job after reconnection. */
  onBusyChange(busy: boolean): void
}

const languageNames = { eng: 'English', chi_sim: 'Simplified Chinese', chi_tra: 'Traditional Chinese' }

/** Analyze one selected image through explicit local providers and keep cancellation independent of their physical drain. */
export function VisionAnalysisPanel({ api, chatId, projectId, images, selectedImageId, ready, disabled, onBusyChange }: VisionAnalysisPanelProps) {
  useTranslation()
  const [analysis, setAnalysis] = useState<VisionAnalysisState | null>(null)
  const [language, setLanguage] = useState<VisionOcrLanguage>('eng')
  const [operation, setOperation] = useState<'readiness' | 'ocr' | 'model-readiness' | 'model' | null>(null)
  const [modelName, setModelName] = useState('')
  const [question, setQuestion] = useState(t("Describe this image."))
  const [cancelPending, setCancelPending] = useState(false)
  const [leased, setLeased] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<'model' | 'ocr'>('model')
  const version = useRef(0)
  const requestActive = useRef(false)
  const observedBusy = useRef(false)
  const revision = useRef(-1)
  const pollActive = useRef(false)
  const metadataActive = useRef(false)

  const report = useCallback(() => {
    const busy = requestActive.current || observedBusy.current
    setLeased(busy)
    onBusyChange(busy)
  }, [onBusyChange])

  const publish = useCallback((next: VisionAnalysisState, token: number): void => {
    if (token !== version.current || next.owner.chatId !== chatId || next.owner.projectId !== projectId
      || next.revision < revision.current) return
    revision.current = next.revision
    setAnalysis(next)
  }, [chatId, projectId])
  const observe = useCallback((busy: boolean, token: number): void => {
    if (token !== version.current) return
    observedBusy.current = busy
    report()
  }, [report])

  useEffect(() => {
    const token = ++version.current
    revision.current = -1
    queueMicrotask(() => {
      if (token !== version.current) return
      setAnalysis(null); setError(null)
      if (api !== undefined) void api.getVisionAnalysisActivity(chatId).then((next) => {
        observe(next.busy, token)
      }, () => {})
      if (api !== undefined && ready) void api.getVisionAnalysis(chatId).then((next) => {
        publish(next, token)
      }, () => { if (token === version.current) setError('Could not load local OCR state. Check availability after reconnecting.') })
    })
    return () => { version.current += 1 }
  }, [api, chatId, projectId, ready, publish, observe])

  useEffect(() => {
    if (api === undefined || !leased) return
    const token = version.current
    // Physical capacity stays observable while Python is disconnected. Evidence
    // hydration may await that service, so it must never delay offline drain
    // polling or release a lease based only on cleared owner-scoped evidence.
    const timer = window.setInterval(() => {
      if (pollActive.current || token !== version.current) return
      pollActive.current = true
      void api.getVisionAnalysisActivity(chatId).then((next) => {
        observe(next.busy, token)
        if (token === version.current && ready && !metadataActive.current) {
          metadataActive.current = true
          void api.getVisionAnalysis(chatId).then((state) => { publish(state, token) }, () => {}).finally(() => {
            metadataActive.current = false
          })
        }
      }, () => {}).finally(() => {
        pollActive.current = false
      })
    }, 300)
    return () => { window.clearInterval(timer) }
  }, [api, chatId, projectId, ready, leased, publish, observe])

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

  return <section className="vision-analysis" aria-label={t("Visual analysis")} aria-busy={leased}>
    <div className="vision-analysis-heading">
      <ToolTabs<'model' | 'ocr'> idPrefix="vision-analysis-kind" label={t("Image analysis")} value={view} onValueChange={setView}
        items={[{ value: 'model', label: t("Describe image") }, { value: 'ocr', label: t("Read text") }]} />
      {leased &&
      <button type="button" disabled={!ready || !leased || cancelPending || visible?.cancelRequested === true}
        onClick={() => { void cancel() }}>{t("Cancel analysis")}</button>}
    </div>
    {leased && <p role="status">{visible?.cancelRequested ? t("Cancellation requested. Waiting for the local analysis operation to finish.")
      : phase === 'model-readiness' ? t("Checking installed local vision models…")
        : phase === 'model' ? t("The local vision model is analyzing the selected image…")
          : phase === 'readiness' ? t("Checking local Tesseract and language data…") : t("Local OCR is running…")}</p>}
    {/* These providers share one physical request owner. Switching views only
        changes visibility; it cannot abandon the request or its cancellation. */}
    <ToolTabPanel idPrefix="vision-analysis-kind" value="ocr" activeValue={view}>
    <section className="vision-provider" aria-label={t("Local OCR")}>
      <div className="vision-analysis-controls">
      <button type="button" disabled={locked} onClick={() => { void run('readiness') }}>{t("Check OCR")}</button>
      <label>{t("OCR language")}<select value={language} disabled={locked} onChange={(event) => { setLanguage(event.currentTarget.value as VisionOcrLanguage) }}>
        <option value="eng">{t("English")}</option>
        <option value="chi_sim+eng">{t("Simplified Chinese + English")}</option>
        <option value="chi_tra+eng">{t("Traditional Chinese + English")}</option>
      </select></label>
      <button type="button" className="tool-primary" disabled={locked || selected === undefined || !available} onClick={() => { void run('ocr') }}>{t("Run OCR")}</button>
      </div>
    <p className="vision-provider-status" role="status">{readiness === null ? t("Check OCR to connect your local text reader.")
        : readiness.status === 'ready' ? t("Tesseract {value1} is ready.", { value1: readiness.version ?? '' })
          : readiness.status === 'missing_engine' ? t("Tesseract is missing or not configured. Install a local engine before checking again.")
            : readiness.status === 'missing_languages' ? t("Required Tesseract language data is missing. Install English and the Chinese data you need.")
              : t("Local Tesseract is unavailable. Check the local installation before retrying.")}</p>
    {readiness !== null && <details><summary>{t("Language availability")}</summary><p>{t("Installed language data: ")}{readiness.languages.length === 0 ? t("None")
      : readiness.languages.map((installed) => t(languageNames[installed])).join(', ')}.</p></details>}
    {readiness?.status === 'ready' && !available && <p>{t("The selected language requires additional local Tesseract language data.")}</p>}
    {!leased && visible?.ocr.error !== null && visible?.ocr.error !== undefined && <p role="alert">{t("Local OCR ended: ")}{formatUiError(visible.ocr.error.message)}.</p>}
    {evidence !== null && evidence !== undefined && selected !== undefined && <div className="vision-ocr-result" role="region" aria-label={t("OCR evidence")}>
      <strong>{t("OCR extraction · ")}{selected.fileName}</strong>
      <pre aria-label={t("Extracted OCR text")}>{evidence.text || t("No text was recognized.")}</pre>
      {/* Bound both dimensions while preserving the original aspect ratio; a
          valid narrow/tall image must not produce a million-pixel review row. */}
      <div className="vision-ocr-positions" style={{ aspectRatio: `${selected.width} / ${selected.height}`,
        width: `min(100%, ${Math.min(20, 12 * selected.width / selected.height)}rem)` }} aria-label={t("OCR word positions")}>
        <img src={selected.previewDataUrl} alt={t("OCR positions in {fileName}", { fileName: selected.fileName })} />
        {evidence.words.map((word, index) => <span key={index} title={`${word.text} · ${Math.round(word.confidence * 100)}%`}
          style={{ left: `${word.x / evidence.width * 100}%`, top: `${word.y / evidence.height * 100}%`,
            width: `${word.width / evidence.width * 100}%`, height: `${word.height / evidence.height * 100}%` }} />)}
      </div>
      <details><summary>{t("Word positions and confidence (")}{evidence.words.length})</summary><ol className="vision-ocr-words">
        {evidence.words.map((word, index) => <li key={index}>{word.text} · {Math.round(word.confidence * 100)}{t("% · x ")}{word.x}{t(", y ")}{word.y}{t(", width ")}{word.width}{t(", height ")}{word.height}</li>)}
      </ol></details>
    </div>}
    </section>
    </ToolTabPanel>
    <ToolTabPanel idPrefix="vision-analysis-kind" value="model" activeValue={view}>
    <section className="vision-provider" aria-label={t("Local vision model")}>
      <div className="vision-analysis-controls">
        <button type="button" disabled={locked} onClick={() => { void run('model-readiness') }}>{t("Check vision models")}</button>
        <label>{t("Vision model")}<select value={selectedModel} disabled={locked} onChange={(event) => { setModelName(event.currentTarget.value) }}>
          <option value="">{t("Select a vision model")}</option>
          {(modelReadiness?.models ?? []).map((model) => <option key={model} value={model}>{model}</option>)}
        </select></label>
      </div>
      <p className="vision-provider-status" role="status">{modelReadiness === null ? t("Check vision models to get started.")
        : modelReadiness.status === 'ready' ? modelReadiness.models.length === 1 ? t("One installed vision model is available.") : t("{count} installed vision models are available.", { count: modelReadiness.models.length })
          : modelReadiness.status === 'no_vision_model' ? t("No installed model advertises vision support. Configure a local Ollama vision model and check again.")
            : t("Local vision models are unavailable. Start local Ollama and check again.")}</p>
      <label className="vision-model-question">{t("Question about image")}<textarea rows={4} maxLength={4096} value={question}
        disabled={locked} onChange={(event) => { setQuestion(event.currentTarget.value) }} /></label>
      {questionBytes > 3500 && <p>{questionBytes} {t(" / 4096 UTF-8 bytes.")}</p>}
      {!questionValid && <p>{t("Enter a question within the byte limit before asking.")}</p>}
      <button type="button" className="tool-primary" disabled={locked || selected === undefined || !modelAvailable || !questionValid}
        onClick={() => { void run('model') }}>{t("Ask about image")}</button>
      <HelpHint label={t("Local vision model")}>{t("Runs locally. Review model answers for accuracy.")}</HelpHint>
      {!leased && visible?.model.error !== null && visible?.model.error !== undefined && <p role="alert">{t("Local vision model ended: ")}{formatUiError(visible.model.error.message)}.</p>}
      {inference !== null && inference !== undefined && <div className="vision-ocr-result" role="region" aria-label={t("Model inference")}>
        <strong>{t("Model inference · ")}{inference.model}</strong>
        <p>{t("Question: ")}{inference.question}</p>
        <pre aria-label={t("Local model inference")}>{inference.text || t("The local model returned no description.")}</pre>
        {inference.truncated && <p role="alert">{t("The model response was truncated. Review it as incomplete.")}</p>}
      </div>}
    </section>
    </ToolTabPanel>
    {error !== null && <p role="alert">{formatUiError(error)}</p>}
  </section>
}
