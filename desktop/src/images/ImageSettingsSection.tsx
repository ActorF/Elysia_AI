/**
 * @fileoverview Guide local image profile setup through Main-owned installation review.
 * This section edits descriptive metadata only. Native pickers choose runtime
 * files, and Main verifies their hashes and asks for explicit trust confirmation.
 */

import { useEffect, useRef, useState } from 'react'
import type { ImageAssetImportParams, ImageLoraMetadata, ImageProfileMetadata, ImageSettingsSummary } from '../../electron/image-client.ts'
import './ImageSettingsSection.css'

interface ImageSettingsSectionProps {
  /** Defer expensive local file checks while this settings surface is hidden. */
  active?: boolean
}

const initialMetadata: ImageProfileMetadata = {
  profileId: 'local-sdxl', displayName: '本地 SDXL', promptDialect: 'sdxl-natural',
  checkpointSource: '', checkpointLicense: '', defaultSizePresetId: 'square',
}
const initialLoraMetadata: ImageLoraMetadata = {
  loraId: 'local-lora', displayName: '本地 LoRA', targetProfileId: '', source: '', license: '',
}
const stateLabels: Record<ImageSettingsSummary['state'], string> = {
  unconfigured: '尚未配置本地图片生成', ready: '本地图片生成已就绪',
  missing_model: '缺少已配置的模型', hash_mismatch: '文件校验不一致', invalid: '图片配置需要检查',
  recovery_required: '有未完成的图片任务需要恢复',
}
const dialectLabels: Record<string, string> = {
  'sdxl-natural': 'SDXL 自然语言', 'pony-tags': 'Pony 标签', 'illustrious-tags': 'Illustrious 标签',
}
const presetLabels: Record<string, string> = { square: '方形', portrait: '竖图', landscape: '横图' }

function validText(value: string, maximum: number): boolean {
  return value.trim().length > 0 && [...value.trim()].length <= maximum && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(value)
}

function validMetadata(metadata: ImageProfileMetadata): boolean {
  return /^[a-z][a-z0-9_-]{0,63}$/u.test(metadata.profileId)
    && validText(metadata.displayName, 80) && !/[\\/]|(?:^|\s)[a-z]:/iu.test(metadata.displayName)
    && validText(metadata.checkpointSource, 512) && validText(metadata.checkpointLicense, 512)
}

function validLoraMetadata(metadata: ImageLoraMetadata): boolean {
  return /^[a-z][a-z0-9_-]{0,63}$/u.test(metadata.loraId)
    && validText(metadata.displayName, 80) && !/[\\/]|(?:^|\s)[a-z]:/iu.test(metadata.displayName)
    && validText(metadata.source, 512) && validText(metadata.license, 512)
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : '无法检查或保存本地图片设置，请重试。'
}

/** Review local model readiness and configure one explicitly trusted installation. */
export function ImageSettingsSection({ active = true }: ImageSettingsSectionProps) {
  const [status, setStatus] = useState<ImageSettingsSummary | null>(null)
  const [metadata, setMetadata] = useState<ImageProfileMetadata>(initialMetadata)
  const [loraMetadata, setLoraMetadata] = useState<ImageLoraMetadata>(initialLoraMetadata)
  const [loraEdited, setLoraEdited] = useState(false)
  const [importKind, setImportKind] = useState<ImageAssetImportParams['kind'] | null>(null)
  const [loading, setLoading] = useState(active && window.elysiaDesktop !== undefined)
  const [configuring, setConfiguring] = useState(false)
  const [edited, setEdited] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const mounted = useRef(true)
  const operation = useRef(0)
  const installing = useRef(false)
  const metadataValid = validMetadata(metadata)
  const targetProfileId = status?.profiles.some((profile) => profile.profileId === loraMetadata.targetProfileId)
    ? loraMetadata.targetProfileId : status?.defaultProfileId ?? status?.profiles[0]?.profileId ?? ''
  const loraValid = validLoraMetadata(loraMetadata) && status?.profiles.some((profile) => profile.profileId === targetProfileId) === true
  const api = window.elysiaDesktop

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    if (!active || api === undefined || installing.current) return
    let disposed = false
    const revision = ++operation.current
    void Promise.resolve().then(async () => {
      if (disposed) return
      setLoading(true)
      setError(null)
      try {
        const next = await api.getImageSettings()
        if (!disposed && revision === operation.current) setStatus(next)
      } catch (failure) {
        if (!disposed && revision === operation.current) setError(errorText(failure))
      } finally {
        if (!disposed && revision === operation.current) setLoading(false)
      }
    })
    return () => { disposed = true }
  }, [active, api])

  function edit(values: Partial<ImageProfileMetadata>): void {
    setMetadata((previous) => ({ ...previous, ...values }))
    setEdited(true)
    setNotice(null)
  }

  function editLora(values: Partial<ImageLoraMetadata>): void {
    setLoraMetadata((previous) => ({ ...previous, ...values }))
    setLoraEdited(true)
    setNotice(null)
  }

  async function importAsset(kind: ImageAssetImportParams['kind']): Promise<void> {
    if (api === undefined || loading || installing.current || (kind === 'checkpoint' ? !metadataValid : !loraValid)) return
    const params: ImageAssetImportParams = kind === 'checkpoint' ? { kind, metadata: {
      ...metadata, displayName: metadata.displayName.trim(), checkpointSource: metadata.checkpointSource.trim(),
      checkpointLicense: metadata.checkpointLicense.trim(),
    } } : { kind, metadata: {
      ...loraMetadata, targetProfileId, displayName: loraMetadata.displayName.trim(),
      source: loraMetadata.source.trim(), license: loraMetadata.license.trim(),
    } }
    const revision = ++operation.current
    const previousRevision = status?.revision ?? 0
    const hadProfiles = Boolean(status?.profiles.length)
    installing.current = true
    setConfiguring(true)
    setImportKind(kind)
    setError(null)
    setNotice(null)
    try {
      const next = await api.importImageAsset(params)
      if (!mounted.current || revision !== operation.current) return
      setStatus(next)
      setNotice(next.revision === previousRevision ? '导入已取消，图片配置未更改。'
        : kind === 'lora' ? 'LoRA 已导入，可在对应模型下选择。'
          : hadProfiles ? 'Checkpoint 已导入，已有模型与默认选择已保留。' : 'Checkpoint 已导入，本地图片模型已配置。')
    } catch (failure) {
      if (mounted.current && revision === operation.current) setError(errorText(failure))
    } finally {
      installing.current = false
      if (mounted.current && revision === operation.current) { setConfiguring(false); setImportKind(null) }
    }
  }

  async function refresh(): Promise<void> {
    if (api === undefined || loading || configuring) return
    const revision = ++operation.current
    setLoading(true)
    setError(null)
    setNotice(null)
    try {
      const next = await api.getImageSettings()
      if (mounted.current && revision === operation.current) setStatus(next)
    } catch (failure) {
      if (mounted.current && revision === operation.current) setError(errorText(failure))
    } finally {
      if (mounted.current && revision === operation.current) setLoading(false)
    }
  }

  async function configure(): Promise<void> {
    if (api === undefined || loading || installing.current || !metadataValid) return
    const revision = ++operation.current
    const previousRevision = status?.revision ?? 0
    installing.current = true
    setConfiguring(true)
    setError(null)
    setNotice(null)
    try {
      const next = await api.configureImageInstallation({
        profileId: metadata.profileId, displayName: metadata.displayName.trim(),
        promptDialect: metadata.promptDialect, checkpointSource: metadata.checkpointSource.trim(),
        checkpointLicense: metadata.checkpointLicense.trim(), defaultSizePresetId: metadata.defaultSizePresetId,
      })
      if (!mounted.current || revision !== operation.current) return
      setStatus(next)
      setNotice(next.revision === previousRevision ? '安装配置未更改。'
        : next.ready ? '配置已保存，本地图片模型已就绪。' : '配置已保存，请根据状态检查模型与运行环境。')
    } catch (failure) {
      if (mounted.current && revision === operation.current) setError(errorText(failure))
    } finally {
      installing.current = false
      if (mounted.current && revision === operation.current) setConfiguring(false)
    }
  }

  async function recover(): Promise<void> {
    if (api === undefined || loading || installing.current) return
    const revision = ++operation.current
    installing.current = true
    setConfiguring(true)
    setError(null)
    setNotice(null)
    try {
      const next = await api.recoverImageRuntime()
      if (!mounted.current || revision !== operation.current) return
      setStatus(next)
      setNotice(next.state === 'recovery_required' ? '恢复尚未完成，临时文件继续保留。'
        : '恢复检查完成，未完成任务的文件已保留。')
    } catch (failure) {
      if (mounted.current && revision === operation.current) setError(errorText(failure))
    } finally {
      installing.current = false
      if (mounted.current && revision === operation.current) setConfiguring(false)
    }
  }

  return <section className="settings-section image-settings" aria-labelledby="image-settings-heading" aria-busy={loading || configuring}
    onKeyDown={(event) => {
      // These controls live inside the shared Settings form, but installation
      // review must never submit or overwrite unrelated global preferences.
      if (event.key === 'Enter' && event.target instanceof HTMLInputElement) event.preventDefault()
    }}>
    <div className="settings-section-heading"><h2 id="image-settings-heading">本地图片生成</h2>
      <p>连接已安装的 ComfyUI 与 SDXL 模型，图片保存在本机画廊。</p></div>
    {api === undefined && <p role="alert">请在 Elysia 桌面应用中配置本地图片生成。</p>}
    <div className={`image-settings-readiness ${status?.ready ? 'is-ready' : ''}`} aria-label="本地图片模型状态">
      <strong>{loading ? '正在检查模型与运行环境…' : status ? stateLabels[status.state] : '尚未读取图片配置'}</strong>
      {status && <p>{status.message}</p>}
      {status?.state === 'missing_model' && <p>请恢复已确认的模型文件，或重新选择并确认本地安装。</p>}
      {status?.state === 'hash_mismatch' && <p>文件已发生变化。请确认来源与许可证，再重新选择安装以审阅新的校验值。</p>}
      {status?.state === 'invalid' && <p>请检查安装是否完整。配置文档损坏时，可重新选择并确认安装进行恢复；原配置会保留备份。</p>}
      {status?.state === 'recovery_required' && <div>
        <p>通过系统确认窗口，将未完成任务移入私有隔离目录并保留全部文件，然后重新检查运行环境。</p>
        <button type="button" className="secondary-button image-settings-recovery" onClick={() => { void recover() }} disabled={loading || configuring}>
          {configuring ? '正在恢复…' : '隔离保留并恢复图片生成'}
        </button>
      </div>}
      {notice !== null && <p role="status">{notice}</p>}
    </div>
    {error !== null && <p role="alert">{error}</p>}
    {status?.profiles.length ? <div className="image-settings-profiles" aria-label="已配置图片模型">
      {status.profiles.map((profile) => <article key={profile.profileId}>
        <div><strong>{profile.displayName}</strong><span>{profile.profileId}{status.defaultProfileId === profile.profileId ? ' · 默认模型' : ''}</span></div>
        <p>{dialectLabels[profile.promptDialect] ?? profile.promptDialect}</p>
        <ul>{profile.sizePresets.map((preset) => <li key={preset.presetId}>{presetLabels[preset.presetId] ?? preset.presetId} · {preset.width} × {preset.height}
          {preset.presetId === profile.defaultSizePresetId ? ' · 默认尺寸' : ''}</li>)}</ul>
        {profile.loras?.length ? <div className="image-settings-loras" aria-label={`${profile.displayName} 已配置 LoRA`}>
          <h3>LoRA</h3>
          {profile.loras.map((lora) => <div key={lora.loraId}>
            <strong>{lora.displayName}</strong><span>{lora.loraId} · {lora.available ? '可用' : '不可用'}</span><p>{lora.message}</p>
          </div>)}
        </div> : null}
      </article>)}
    </div> : null}
    <fieldset disabled={api === undefined || loading || configuring}>
      <legend>导入 Checkpoint</legend>
      <p>填写模型资料后选择本地模型文件。首次导入还会选择 ComfyUI 与 Python；再次导入会保留已有模型与默认选择。</p>
      <div className="settings-field-grid">
        <label className="settings-field"><span>图片模型名称</span><input aria-label="图片模型名称" value={metadata.displayName} maxLength={80} aria-required="true"
          onChange={(event) => { edit({ displayName: event.currentTarget.value }) }} /></label>
        <label className="settings-field"><span>图片模型 ID</span><input aria-label="图片模型 ID" value={metadata.profileId} maxLength={64} spellCheck={false}
          aria-invalid={edited && !/^[a-z][a-z0-9_-]{0,63}$/u.test(metadata.profileId)} aria-required="true"
          onChange={(event) => { edit({ profileId: event.currentTarget.value }) }} /><small>以小写字母开头，仅使用小写字母、数字、下划线或连字符。</small></label>
        <label className="settings-field"><span>模型提示词格式</span><select aria-label="模型提示词格式" value={metadata.promptDialect}
          onChange={(event) => { edit({ promptDialect: event.currentTarget.value as ImageProfileMetadata['promptDialect'] }) }}>
          <option value="sdxl-natural">SDXL 自然语言</option><option value="pony-tags">Pony 标签</option><option value="illustrious-tags">Illustrious 标签</option>
        </select></label>
        <label className="settings-field"><span>默认图片尺寸</span><select aria-label="默认图片尺寸" value={metadata.defaultSizePresetId}
          onChange={(event) => { edit({ defaultSizePresetId: event.currentTarget.value as ImageProfileMetadata['defaultSizePresetId'] }) }}>
          <option value="square">方形 · 1024 × 1024</option><option value="portrait">竖图 · 832 × 1216</option><option value="landscape">横图 · 1216 × 832</option>
        </select></label>
        <label className="settings-field image-settings-wide"><span>模型来源</span><input aria-label="模型来源" value={metadata.checkpointSource} maxLength={512}
          placeholder="模型发布者的来源链接或说明" aria-required="true" onChange={(event) => { edit({ checkpointSource: event.currentTarget.value }) }} /></label>
        <label className="settings-field image-settings-wide"><span>模型许可证</span><input aria-label="模型许可证" value={metadata.checkpointLicense} maxLength={512}
          placeholder="填写所选模型实际适用的许可证" aria-required="true" onChange={(event) => { edit({ checkpointLicense: event.currentTarget.value }) }} />
          <small>请核实所选模型的来源、许可及适用限制。此处不会自动下载模型或依赖。</small></label>
      </div>
    </fieldset>
    {edited && !metadataValid && <p className="image-settings-validation">请填写有效的模型名称、ID、来源和许可证，再选择本地安装。</p>}
    <p className="image-settings-consent">选择后会校验文件，并在系统确认窗口显示所选安装、模型来源与许可证。仅在你明确确认后保存。</p>
    <div className="image-settings-actions">
      <button type="button" className="primary-button" disabled={api === undefined || loading || configuring || !metadataValid}
        onClick={() => { void importAsset('checkpoint') }}>{importKind === 'checkpoint' ? '正在导入 Checkpoint…' : '导入 Checkpoint'}</button>
      <button type="button" className="secondary-button" disabled={api === undefined || loading || configuring || !metadataValid}
        onClick={() => { void configure() }}>{configuring ? '正在选择与校验安装…' : '选择并确认本地安装'}</button>
      <button type="button" className="secondary-button" disabled={api === undefined || loading || configuring} onClick={() => { void refresh() }}>检查模型与运行环境</button>
    </div>
    <fieldset className="image-settings-lora-import" disabled={api === undefined || loading || configuring || !status?.profiles.length}>
      <legend>导入 LoRA</legend>
      <p>选择适用的图片模型并填写 LoRA 资料，再通过系统窗口选择文件。导入后可在生成器中调整模型与 CLIP 强度。</p>
      <div className="settings-field-grid">
        <label className="settings-field"><span>LoRA 名称</span><input aria-label="LoRA 名称" value={loraMetadata.displayName} maxLength={80}
          onChange={(event) => { editLora({ displayName: event.currentTarget.value }) }} /></label>
        <label className="settings-field"><span>LoRA ID</span><input aria-label="LoRA ID" value={loraMetadata.loraId} maxLength={64} spellCheck={false}
          aria-invalid={loraEdited && !/^[a-z][a-z0-9_-]{0,63}$/u.test(loraMetadata.loraId)}
          onChange={(event) => { editLora({ loraId: event.currentTarget.value }) }} /><small>以小写字母开头，仅使用小写字母、数字、下划线或连字符。</small></label>
        <label className="settings-field image-settings-wide"><span>LoRA 适用模型</span><select aria-label="LoRA 适用模型" value={targetProfileId}
          onChange={(event) => { editLora({ targetProfileId: event.currentTarget.value }) }}>
          {!status?.profiles.length && <option value="">请先导入图片模型</option>}
          {status?.profiles.map((profile) => <option key={profile.profileId} value={profile.profileId}>{profile.displayName}</option>)}
        </select></label>
        <label className="settings-field image-settings-wide"><span>LoRA 来源</span><input aria-label="LoRA 来源" value={loraMetadata.source} maxLength={512}
          placeholder="LoRA 发布者的来源链接或说明" onChange={(event) => { editLora({ source: event.currentTarget.value }) }} /></label>
        <label className="settings-field image-settings-wide"><span>LoRA 许可证</span><input aria-label="LoRA 许可证" value={loraMetadata.license} maxLength={512}
          onChange={(event) => { editLora({ license: event.currentTarget.value }) }} /></label>
      </div>
      {loraEdited && !loraValid && <p className="image-settings-validation">请填写有效的 LoRA 名称、ID、来源与许可证，并选择适用模型。</p>}
      <div className="image-settings-actions"><button type="button" className="primary-button" disabled={!loraValid}
        onClick={() => { void importAsset('lora') }}>{importKind === 'lora' ? '正在导入 LoRA…' : '导入 LoRA'}</button></div>
    </fieldset>
    {configuring && <p role="status">请完成系统选择与确认。模型校验可能需要一段时间。</p>}
  </section>
}
