/**
 * @fileoverview Present trusted local character generation and the private Gallery.
 * Main owns cancellation, preview capabilities, and native export; this surface
 * sends structured descriptions and logical artifact/profile identifiers only.
 */

import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { ImagePreviewCapability, ImageSettingsSummary } from '../../electron/image-client.ts'
import type { ImageArtifact, ImageGenerateParams, ImageGalleryListResult } from '../../electron/protocol.ts'
import { MAX_IMAGE_PROMPT_COMPONENT_CODE_POINTS } from '../../electron/protocol.ts'
import './ImageGenerator.css'
import { Icon } from '../design-system/Icon.tsx'

interface ImageGeneratorProps {
  sidebarOpen?: boolean
  onToggleSidebar?(): void
  /** Preserve task ownership while a hidden studio suspends storage refreshes. */
  active?: boolean
  /** Open the application's trusted local image installation settings. */
  onOpenSettings?(): void
}

type PromptField = 'appearance' | 'outfit' | 'pose' | 'scene' | 'style' | 'camera' | 'lighting' | 'negativePrompt'
const promptFields: Array<{ key: PromptField; label: string; placeholder: string }> = [
  { key: 'appearance', label: '外观描述', placeholder: '例如：成年女性，银色长发，紫色眼睛' },
  { key: 'outfit', label: '服装', placeholder: '例如：白色长裙与银色耳饰' },
  { key: 'pose', label: '姿势', placeholder: '例如：自然站姿，轻轻侧身' },
  { key: 'scene', label: '场景', placeholder: '例如：黄昏的花园' },
  { key: 'style', label: '风格', placeholder: '例如：柔和的数字插画' },
  { key: 'camera', label: '镜头', placeholder: '例如：半身肖像，平视' },
  { key: 'lighting', label: '光线', placeholder: '例如：温暖逆光' },
  { key: 'negativePrompt', label: '负面词', placeholder: '例如：模糊，畸形的手' },
]
const pageSize = 12
const stageLabels: Record<string, string> = {
  validating: '检查生成描述', probing: '检查本地模型', submitting: '准备生成',
  sampling: '正在生成图片', verifying: '验证图片并保存', completed: '图片已保存',
}
const readinessLabels: Record<ImageSettingsSummary['state'], string> = {
  unconfigured: '尚未配置本地图片模型', missing_model: '缺少已配置的模型文件',
  hash_mismatch: '模型或运行文件校验不一致', invalid: '本地图片运行环境需要检查',
  recovery_required: '请到图片设置恢复未完成任务',
  ready: '本地图片模型已就绪',
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

function artifactTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

/** Generate one confirmed original adult character and manage its saved images. */
export function ImageGenerator({ active = true, onOpenSettings, onToggleSidebar, sidebarOpen }: ImageGeneratorProps) {
  const [settings, setSettings] = useState<ImageSettingsSummary | null>(null)
  const [settingsLoading, setSettingsLoading] = useState(active && window.elysiaDesktop !== undefined)
  const [settingsError, setSettingsError] = useState<string | null>(
    window.elysiaDesktop === undefined ? '请在 Elysia 桌面应用中使用图片生成。' : null,
  )
  const [profileId, setProfileId] = useState('')
  const [sizePresetId, setSizePresetId] = useState('')
  const [prompts, setPrompts] = useState<Record<PromptField, string>>({
    appearance: '', outfit: '', pose: '', scene: '', style: '', camera: '', lighting: '', negativePrompt: '',
  })
  const [seed, setSeed] = useState('0')
  const [confirmed, setConfirmed] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [progress, setProgress] = useState({ completed: 0, stage: '' })
  const [generationError, setGenerationError] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [gallery, setGallery] = useState<ImageGalleryListResult | null>(null)
  const [galleryLoading, setGalleryLoading] = useState(active && window.elysiaDesktop !== undefined)
  const [galleryError, setGalleryError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ImagePreviewCapability | null>(null)
  const [previewLoading, setPreviewLoading] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [artifactBusy, setArtifactBusy] = useState<string | null>(null)
  const [deleteCandidate, setDeleteCandidate] = useState<string | null>(null)
  const mounted = useRef(true)
  const running = useRef(false)
  const progressRequestId = useRef<string | null>(null)
  const galleryRevision = useRef(0)
  const previewRevision = useRef(0)
  const activeView = useRef(active)
  const selection = useRef({ profileId: '', sizePresetId: '' })
  const selectedProfile = settings?.profiles.find((profile) => profile.profileId === profileId)
  const seedValid = /^\d+$/.test(seed) && Number.isSafeInteger(Number(seed)) && Number(seed) >= 0
  const canGenerate = settings?.ready === true && selectedProfile !== undefined
    && selectedProfile.sizePresets.some((preset) => preset.presetId === sizePresetId)
    && prompts.appearance.trim().length > 0 && confirmed && seedValid && !generating
    && !settingsLoading && settingsError === null

  useEffect(() => {
    mounted.current = true
    const api = window.elysiaDesktop
    if (api === undefined) return
    const unsubscribe = api.onBackendEvent((event) => {
      if (event.type !== 'progress' || event.operation !== 'image.generate' || !running.current) return
      // The first current-operation event binds the display to one request.
      // Late events from an older request cannot move an already bound bar.
      if (progressRequestId.current !== null && progressRequestId.current !== event.requestId) return
      progressRequestId.current = event.requestId
      setProgress({ completed: Math.max(0, Math.min(100, event.completed)), stage: event.message ?? '' })
    })
    return () => {
      mounted.current = false
      unsubscribe()
    }
  }, [])

  useEffect(() => {
    activeView.current = active
    if (!active) return
    let disposed = false
    const api = window.elysiaDesktop
    if (!api) return
    const imageRevision = ++galleryRevision.current
    // A hidden studio retains cancellation state but does not probe model files
    // or Gallery storage until navigation makes the surface active again.
    void Promise.resolve().then(async () => {
      if (disposed) return
      setSettingsLoading(true)
      setGalleryLoading(true)
      setSettingsError(null)
      setGalleryError(null)
      const [setup, images] = await Promise.allSettled([
        api.getImageSettings(), api.getImageGallery({ limit: pageSize, offset: 0 }),
      ])
      if (disposed) return
      if (setup.status === 'fulfilled') {
        setSettings(setup.value)
        const chosen = setup.value.profiles.find((profile) => profile.profileId === selection.current.profileId)
          ?? setup.value.profiles.find((profile) => profile.profileId === setup.value.defaultProfileId)
          ?? setup.value.profiles[0]
        const size = chosen?.sizePresets.some((preset) => preset.presetId === selection.current.sizePresetId)
          ? selection.current.sizePresetId : chosen?.defaultSizePresetId ?? ''
        selection.current = { profileId: chosen?.profileId ?? '', sizePresetId: size }
        setProfileId(selection.current.profileId)
        setSizePresetId(size)
      } else setSettingsError(errorText(setup.reason, '无法读取本地图片设置。'))
      if (imageRevision === galleryRevision.current) {
        if (images.status === 'fulfilled') setGallery(images.value)
        else if (setup.status === 'fulfilled' && setup.value.configured) {
          setGalleryError(errorText(images.reason, '无法加载画廊。'))
        }
        setGalleryLoading(false)
      }
      setSettingsLoading(false)
    })
    return () => { disposed = true }
  }, [active])

  async function refreshSettings(): Promise<void> {
    const api = window.elysiaDesktop
    if (!api || settingsLoading || generating) return
    setSettingsLoading(true)
    setSettingsError(null)
    try {
      const next = await api.getImageSettings()
      if (!mounted.current) return
      setSettings(next)
      const chosen = next.profiles.find((profile) => profile.profileId === profileId)
        ?? next.profiles.find((profile) => profile.profileId === next.defaultProfileId) ?? next.profiles[0]
      setProfileId(chosen?.profileId ?? '')
      const size = chosen?.sizePresets.some((preset) => preset.presetId === sizePresetId)
        ? sizePresetId : chosen?.defaultSizePresetId ?? ''
      selection.current = { profileId: chosen?.profileId ?? '', sizePresetId: size }
      setSizePresetId(size)
    } catch (error) {
      if (mounted.current) setSettingsError(errorText(error, '无法检查本地模型。'))
    } finally {
      if (mounted.current) setSettingsLoading(false)
    }
  }

  async function loadGallery(offset = 0): Promise<void> {
    const api = window.elysiaDesktop
    if (!api) return
    const revision = ++galleryRevision.current
    setGalleryLoading(true)
    setGalleryError(null)
    try {
      const next = await api.getImageGallery({ limit: pageSize, offset })
      if (mounted.current && revision === galleryRevision.current) setGallery(next)
    } catch (error) {
      if (mounted.current && revision === galleryRevision.current) setGalleryError(errorText(error, '无法加载画廊。'))
    } finally {
      if (mounted.current && revision === galleryRevision.current) setGalleryLoading(false)
    }
  }

  async function openPreview(artifactId: string): Promise<void> {
    const api = window.elysiaDesktop
    if (!api) return
    const revision = ++previewRevision.current
    setPreviewLoading(artifactId)
    setPreviewError(null)
    setPreview(null)
    try {
      const next = await api.openImagePreview(artifactId)
      if (mounted.current && revision === previewRevision.current) setPreview(next)
    } catch (error) {
      if (mounted.current && revision === previewRevision.current) setPreviewError(errorText(error, '无法打开图片预览。'))
    } finally {
      if (mounted.current && revision === previewRevision.current) setPreviewLoading(null)
    }
  }

  async function generate(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const api = window.elysiaDesktop
    if (!api || !canGenerate || running.current) return
    running.current = true
    progressRequestId.current = null
    setGenerating(true)
    setCancelling(false)
    setGenerationError(null)
    setNotice('')
    setProgress({ completed: 0, stage: '' })
    const params: ImageGenerateParams = {
      profileId, sizePresetId, ...prompts, seed: Number(seed), originalFictionalConfirmed: true,
    }
    try {
      const result = await api.generateImage(params)
      if (!mounted.current) return
      setProgress({ completed: 100, stage: 'completed' })
      setNotice('图片已生成并保存到画廊。')
      if (activeView.current) {
        await loadGallery(0)
        await openPreview(result.artifact.artifactId)
      }
    } catch (error) {
      if (mounted.current) setGenerationError(errorText(error, '图片生成未完成，请检查模型后重试。'))
    } finally {
      running.current = false
      progressRequestId.current = null
      if (mounted.current) {
        setGenerating(false)
        setCancelling(false)
      }
    }
  }

  async function cancel(): Promise<void> {
    const api = window.elysiaDesktop
    if (!api || !running.current || cancelling) return
    setCancelling(true)
    try {
      await api.cancelImageGeneration()
      // A cancel acknowledgement does not prove native resources are free.
      // Keep admission closed until the generation promise reaches terminal.
    } catch (error) {
      if (mounted.current) {
        setGenerationError(errorText(error, '无法取消图片任务。'))
        setCancelling(false)
      }
    }
  }

  async function exportArtifact(artifactId: string): Promise<void> {
    const api = window.elysiaDesktop
    if (!api || artifactBusy !== null) return
    setArtifactBusy(artifactId)
    setGalleryError(null)
    try {
      const result = await api.exportImageArtifact(artifactId)
      if (mounted.current && result.exported) setNotice('图片已导出。')
    } catch (error) {
      if (mounted.current) setGalleryError(errorText(error, '无法导出图片。'))
    } finally {
      if (mounted.current) setArtifactBusy(null)
    }
  }

  async function deleteArtifact(artifactId: string): Promise<void> {
    const api = window.elysiaDesktop
    if (!api || artifactBusy !== null) return
    setArtifactBusy(artifactId)
    setGalleryError(null)
    try {
      await api.deleteImageArtifact(artifactId)
      if (!mounted.current) return
      setDeleteCandidate(null)
      ++previewRevision.current
      setPreviewLoading(null)
      if (preview?.artifactId === artifactId) setPreview(null)
      setNotice('图片已从画廊删除。')
      const nextOffset = gallery?.items.length === 1 ? Math.max(0, (gallery?.offset ?? 0) - pageSize) : gallery?.offset ?? 0
      await loadGallery(nextOffset)
    } catch (error) {
      if (mounted.current) setGalleryError(errorText(error, '无法删除图片。'))
    } finally {
      if (mounted.current) setArtifactBusy(null)
    }
  }

  function chooseProfile(value: string): void {
    const size = settings?.profiles.find((profile) => profile.profileId === value)?.defaultSizePresetId ?? ''
    selection.current = { profileId: value, sizePresetId: size }
    setProfileId(value)
    setSizePresetId(size)
  }

  function randomizeSeed(): void {
    setSeed(String(crypto.getRandomValues(new Uint32Array(1))[0]))
  }

  return <section className="image-generator" aria-label="图片生成器">
    {onToggleSidebar && <div className="image-studio-navigation"><button type="button"
      className="icon-button" aria-label="Open navigation" aria-expanded={sidebarOpen}
      aria-controls="app-sidebar" onClick={onToggleSidebar}><Icon name="menu" /></button><span>图片生成</span></div>}
    <header className="image-generator-heading">
      <div><span className="image-eyebrow">ELYSIA · IMAGE STUDIO</span><h1>让想象成为画面</h1><p>描绘一位原创角色，细节由你决定。</p></div>
      {onOpenSettings && <button type="button" onClick={onOpenSettings} disabled={generating}>图片设置</button>}
    </header>
    <section className={`image-readiness ${settings?.ready ? 'is-ready' : ''}`} aria-label="图片运行环境">
      <div><strong>{settingsLoading ? '正在检查本地模型…' : settings ? readinessLabels[settings.state] : '无法读取本地图片设置'}</strong>
        {settings && <p>{settings.message}</p>}</div>
      <button type="button" disabled={settingsLoading || generating} onClick={() => { void refreshSettings() }}>重新检查</button>
    </section>
    {settingsError && <p role="alert">{settingsError}</p>}
    <div className="image-studio-layout">
      <form className="image-generation-form" aria-label="图片生成描述" onSubmit={(event) => { void generate(event) }}>
        <div className="image-form-heading"><h2>描述你的角色</h2><span>仅支持单一、明确成年的原创虚构角色</span></div>
        <fieldset disabled={generating || settingsLoading || !settings?.ready}>
          <div className="image-field-grid">
            <label>模型<select aria-label="图片模型" value={profileId} onChange={(event) => { chooseProfile(event.currentTarget.value) }}>
              {!settings?.profiles.length && <option value="">尚未配置模型</option>}
              {settings?.profiles.map((profile) => <option key={profile.profileId} value={profile.profileId}>{profile.displayName}</option>)}
            </select></label>
            <label>尺寸<select aria-label="图片尺寸" value={sizePresetId} onChange={(event) => { selection.current.sizePresetId = event.currentTarget.value; setSizePresetId(event.currentTarget.value) }}>
              {!selectedProfile && <option value="">请先选择模型</option>}
              {selectedProfile?.sizePresets.map((preset) => <option key={preset.presetId} value={preset.presetId}>{preset.presetId} · {preset.width} × {preset.height}</option>)}
            </select></label>
            {promptFields.map((field) => <label className={field.key === 'appearance' || field.key === 'negativePrompt' ? 'image-field-wide' : ''} key={field.key}>
              <span>{field.label}{field.key === 'appearance' && <span aria-hidden="true"> *</span>}</span>
              <input aria-label={field.label} value={prompts[field.key]} placeholder={field.placeholder}
                maxLength={MAX_IMAGE_PROMPT_COMPONENT_CODE_POINTS} required={field.key === 'appearance'}
                onChange={(event) => { setPrompts({ ...prompts, [field.key]: event.currentTarget.value }) }} />
            </label>)}
            <label className="image-field-wide">Seed<div className="image-seed-control">
              <input aria-label="Seed" inputMode="numeric" value={seed} pattern="[0-9]+" aria-invalid={!seedValid}
                onChange={(event) => { setSeed(event.currentTarget.value) }} />
              <button type="button" onClick={randomizeSeed}>随机 Seed</button></div>
              {!seedValid && <span className="image-field-error">请输入 0 至 9007199254740991 的整数。</span>}
              <small>保留相同 Seed 与描述，便于再次生成。</small>
            </label>
          </div>
          <label className="image-subject-confirmation"><input type="checkbox" checked={confirmed}
            onChange={(event) => { setConfirmed(event.currentTarget.checked) }} />
            我确认：这是单一原创虚构角色，明确年满 18 岁，不涉及真人。</label>
        </fieldset>
        <div className="image-generation-actions">
          <button type="submit" className="image-primary" disabled={!canGenerate}>{generating ? '正在生成…' : '生成图片'}</button>
          {generating && <button type="button" onClick={() => { void cancel() }} disabled={cancelling}>{cancelling ? '取消中…' : '取消生成'}</button>}
        </div>
        {generating && <div className="image-progress"><div><span>{cancelling ? '正在停止本地图片任务…' : stageLabels[progress.stage] ?? '正在准备图片任务…'}</span><span>{progress.completed}%</span></div>
          <progress max={100} value={progress.completed} aria-label="图片生成进度" />
          <p>进度按实际阶段更新，生成阶段可能需要较长时间。</p></div>}
        {generationError && <p role="alert">{generationError}</p>}
        {notice && <p role="status" className="image-notice">{notice}</p>}
      </form>
      <section className="image-gallery" aria-label="图片画廊">
        <div className="image-gallery-heading"><div><h2>画廊</h2><p>你的生成作品，保存在本机。</p></div>
          <button type="button" disabled={galleryLoading} onClick={() => { void loadGallery(gallery?.offset ?? 0) }}>刷新画廊</button></div>
        {previewLoading && <p role="status">正在打开预览…</p>}
        {previewError && <p role="alert">{previewError}</p>}
        {preview && <figure className="image-preview"><img src={preview.url} width={preview.width} height={preview.height} alt="生成图片预览"
          onError={() => { setPreview(null); setPreviewError('预览已失效，请重新打开图片。') }} />
          <figcaption>{preview.width} × {preview.height}<button type="button" onClick={() => { ++previewRevision.current; setPreview(null) }}>关闭预览</button></figcaption></figure>}
        {galleryLoading && <p className="image-empty" role="status">正在加载画廊…</p>}
        {galleryError && <p role="alert">{galleryError}</p>}
        {!galleryLoading && !gallery?.items.length && <div className="image-empty"><span aria-hidden="true">✧</span><strong>从第一张作品开始</strong><p>完成生成后，图片会出现在这里。</p></div>}
        <div className="image-artifact-grid">{gallery?.items.map((artifact: ImageArtifact) => <article className="image-artifact" key={artifact.artifactId} aria-label={`图片 Seed ${artifact.seed}`}>
          <button className="image-artifact-open" type="button" disabled={previewLoading === artifact.artifactId || artifactBusy !== null}
            onClick={() => { void openPreview(artifact.artifactId) }} aria-label={`预览图片 Seed ${artifact.seed}`}>
            <span aria-hidden="true">✧</span><span>{artifact.width} × {artifact.height}</span><span>打开预览</span></button>
          <div className="image-artifact-meta"><strong>Seed {artifact.seed}</strong><time dateTime={artifact.createdAt}>{artifactTime(artifact.createdAt)}</time><span>{artifact.profileId}</span></div>
          <div className="image-artifact-actions"><button type="button" disabled={artifactBusy !== null} onClick={() => { void exportArtifact(artifact.artifactId) }}>导出</button>
            <button type="button" disabled={artifactBusy !== null} onClick={() => { setDeleteCandidate(artifact.artifactId) }}>删除</button></div>
          {deleteCandidate === artifact.artifactId && <section className="image-delete-confirmation" role="alertdialog" aria-label="确认删除图片">
            <p>从画廊永久删除这张图片？已导出的副本会保留。</p><div>
              <button type="button" disabled={artifactBusy !== null} onClick={() => { setDeleteCandidate(null) }}>保留图片</button>
              <button type="button" className="image-danger" disabled={artifactBusy !== null} onClick={() => { void deleteArtifact(artifact.artifactId) }}>确认删除</button>
            </div></section>}
        </article>)}</div>
        {gallery && (gallery.offset > 0 || gallery.hasMore) && <nav className="image-gallery-pagination" aria-label="画廊分页">
          <button type="button" disabled={galleryLoading || gallery.offset === 0} onClick={() => { void loadGallery(Math.max(0, gallery.offset - pageSize)) }}>上一页</button>
          <span>第 {Math.floor(gallery.offset / pageSize) + 1} 页</span><button type="button" disabled={galleryLoading || !gallery.hasMore} onClick={() => { void loadGallery(gallery.offset + pageSize) }}>下一页</button>
        </nav>}
      </section>
    </div>
  </section>
}
