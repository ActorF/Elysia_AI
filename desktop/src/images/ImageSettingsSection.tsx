/**
 * @fileoverview Present local image models and native file imports without metadata forms.
 * Main chooses files, identifies reviewed assets, and confirms installation changes.
 * Detailed metadata remains confined to optional legacy installation repair.
 */

import { t, useTranslation } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'
import { useEffect, useRef, useState } from 'react'
import { HelpHint } from '../design-system/HelpHint.tsx'
import type { ImageAssetImportParams, ImageProfileMetadata, ImageSettingsSummary } from '../../electron/image-client.ts'
import './ImageSettingsSection.css'

interface ImageSettingsSectionProps {
  /** Defer local file checks while this settings surface is hidden. */
  active?: boolean
}

const initialMetadata: ImageProfileMetadata = {
  profileId: 'local-sdxl', displayName: '本地 SDXL', promptDialect: 'sdxl-natural',
  checkpointSource: '', checkpointLicense: '', defaultSizePresetId: 'square',
}
const stateLabels: Record<ImageSettingsSummary['state'], string> = {
  unconfigured: '先添加一个图片模型', ready: '可以开始生成了',
  missing_model: '模型文件缺失', hash_mismatch: '模型文件发生了变化', invalid: '需要修复图片设置',
  recovery_required: '需要恢复上次任务',
}
const stateGuidance: Record<ImageSettingsSummary['state'], string> = {
  unconfigured: '点击导入 Checkpoint，选择本机模型。首次使用会引导你选择运行环境。',
  ready: '已连接本地运行环境，生成的图片会保存到画廊。',
  missing_model: '请恢复原模型文件，或通过高级安装修复重新选择模型。',
  hash_mismatch: '请确认文件来源，再通过高级安装修复重新选择并确认安装。',
  invalid: '请通过高级安装修复重新选择本地安装，原配置会保留备份。',
  recovery_required: '保留未完成任务的文件并恢复运行环境，之后即可继续生成。',
}

function validText(value: string, maximum: number): boolean {
  return value.trim().length > 0 && [...value.trim()].length <= maximum && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(value)
}

function validMetadata(metadata: ImageProfileMetadata): boolean {
  return /^[a-z][a-z0-9_-]{0,63}$/u.test(metadata.profileId)
    && validText(metadata.displayName, 80) && !/[\\/]|(?:^|\s)[a-z]:/iu.test(metadata.displayName)
    && validText(metadata.checkpointSource, 512) && validText(metadata.checkpointLicense, 512)
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : t("无法检查或保存本地图片设置，请重试。")
}

/** Show installed assets and start native imports while retaining owned maintenance state. */
export function ImageSettingsSection({ active = true }: ImageSettingsSectionProps) {
  useTranslation()
  const [status, setStatus] = useState<ImageSettingsSummary | null>(null)
  const [metadata, setMetadata] = useState<ImageProfileMetadata>(initialMetadata)
  const [modelNameEdited, setModelNameEdited] = useState(false)
  // Only the untouched application default follows language changes. Once the
  // user edits this field, its exact value is retained as model metadata.
  const modelDisplayName = modelNameEdited ? metadata.displayName : t('本地 SDXL')
  const metadataValid = validMetadata({ ...metadata, displayName: modelDisplayName })
  const [selectedTarget, setSelectedTarget] = useState('')
  const [maintenance, setMaintenance] = useState<'checkpoint' | 'lora' | 'repair' | 'recovery' | null>(null)
  const [loading, setLoading] = useState(active && window.elysiaDesktop !== undefined)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const mounted = useRef(true)
  const operation = useRef(0)
  const installing = useRef(false)
  const busy = loading || maintenance !== null
  const api = window.elysiaDesktop
  const targetProfileId = status?.profiles.some((profile) => profile.profileId === selectedTarget)
    ? selectedTarget : status?.defaultProfileId ?? status?.profiles[0]?.profileId ?? ''

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

  async function importAsset(kind: 'checkpoint' | 'lora'): Promise<void> {
    if (api === undefined || busy || installing.current || (kind === 'lora' && !targetProfileId)) return
    const params: ImageAssetImportParams = kind === 'checkpoint' ? { kind } : { kind, targetProfileId }
    const revision = ++operation.current
    const previousRevision = status?.revision ?? 0
    installing.current = true
    setMaintenance(kind)
    setError(null)
    setNotice(null)
    try {
      const next = await api.importImageAsset(params)
      if (!mounted.current || revision !== operation.current) return
      setStatus(next)
      setNotice(next.revision === previousRevision ? '导入已取消，图片配置未更改。'
        : kind === 'lora' ? 'LoRA 已添加，可在生成时选择。' : '图片模型已添加。')
    } catch (failure) {
      if (mounted.current && revision === operation.current) setError(errorText(failure))
    } finally {
      installing.current = false
      if (mounted.current && revision === operation.current) setMaintenance(null)
    }
  }

  async function refresh(): Promise<void> {
    if (api === undefined || busy || installing.current) return
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
    if (api === undefined || busy || installing.current || !metadataValid) return
    const revision = ++operation.current
    const previousRevision = status?.revision ?? 0
    installing.current = true
    setMaintenance('repair')
    setError(null)
    setNotice(null)
    try {
      const next = await api.configureImageInstallation({
        ...metadata, displayName: modelDisplayName.trim(),
        checkpointSource: metadata.checkpointSource.trim(), checkpointLicense: metadata.checkpointLicense.trim(),
      })
      if (!mounted.current || revision !== operation.current) return
      setStatus(next)
      setNotice(next.revision === previousRevision ? '安装配置未更改。'
        : next.ready ? '配置已保存，本地图片模型已就绪。' : '配置已保存，请检查模型与运行环境。')
    } catch (failure) {
      if (mounted.current && revision === operation.current) setError(errorText(failure))
    } finally {
      installing.current = false
      if (mounted.current && revision === operation.current) setMaintenance(null)
    }
  }

  async function recover(): Promise<void> {
    if (api === undefined || busy || installing.current) return
    const revision = ++operation.current
    installing.current = true
    setMaintenance('recovery')
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
      if (mounted.current && revision === operation.current) setMaintenance(null)
    }
  }

  return <section className="settings-section image-settings" aria-labelledby="image-settings-heading" aria-busy={busy}
    onKeyDown={(event) => {
      // This section is nested in global Settings. Native maintenance must
      // never submit unrelated preferences through implicit form submission.
      if (event.key === 'Enter' && event.target instanceof HTMLInputElement) event.preventDefault()
    }}>
    <div className="settings-section-heading"><h2 id="image-settings-heading">{t("本地图片生成")}</h2>
      <HelpHint label={t("本地图片生成")}>{t("为爱莉希雅选择主模型与 LoRA，作品保存在本机。")}</HelpHint></div>
    {api === undefined && <p role="alert">{t("请在 Elysia 桌面应用中配置本地图片生成。")}</p>}
    <div className={`image-settings-readiness ${status?.ready ? 'is-ready' : ''}`} aria-label={t("本地图片模型状态")}>
      <span className="image-settings-state-dot" aria-hidden="true" />
      <div><strong>{loading ? t("正在检查模型…") : status ? t(stateLabels[status.state]) : t("尚未读取图片配置")}</strong>
        {status && !status.ready && <p>{t(stateGuidance[status.state])}</p>}
        {status?.state === 'recovery_required' && <button type="button" className="secondary-button image-settings-recovery"
          onClick={() => { void recover() }} disabled={api === undefined || busy}>
          {maintenance === 'recovery' ? t("正在恢复…") : t("保留文件并恢复")}
        </button>}
      </div>
    </div>
    {error !== null && <p role="alert">{formatUiError(error)}</p>}
    {notice !== null && <p className="image-settings-notice" role="status">{t(notice)}</p>}
    <div className="image-settings-actions">
      <button type="button" className="primary-button" disabled={api === undefined || busy}
        onClick={() => { void importAsset('checkpoint') }}>{maintenance === 'checkpoint' ? t("正在导入 Checkpoint…") : t("导入 Checkpoint")}</button>
      <button type="button" className="secondary-button" disabled={api === undefined || busy || !targetProfileId}
        onClick={() => { void importAsset('lora') }}>{maintenance === 'lora' ? t("正在导入 LoRA…") : t("导入 LoRA")}</button>
      <button type="button" className="secondary-button" disabled={api === undefined || busy}
        onClick={() => { void refresh() }}>{t("重新检查")}</button>
    </div>
    {(status?.profiles.length ?? 0) > 1 && <label className="settings-field image-settings-target"><span>{t("LoRA 适用模型")}</span>
      <select aria-label={t("LoRA 适用模型")} value={targetProfileId} disabled={api === undefined || busy}
        onChange={(event) => { setSelectedTarget(event.currentTarget.value) }}>
        {status?.profiles.map((profile) => <option key={profile.profileId} value={profile.profileId}>{profile.displayName}</option>)}
      </select>
    </label>}
    <HelpHint label={t("导入 Checkpoint")}>{t("选择文件后确认即可添加，无需填写模型资料。")}</HelpHint>
    {status?.profiles.length ? <div className="image-settings-profiles" aria-label={t("已配置图片模型")}>
      {status.profiles.map((profile) => <article key={profile.profileId}>
        <div className="image-settings-model-heading"><div><span className="image-settings-eyebrow">{t("主模型")}</span>
          <h3>{profile.displayName}</h3></div>
          {status.defaultProfileId === profile.profileId && <span className="image-settings-default">{t("默认模型")}</span>}
        </div>
        {profile.loras?.length ? <ul className="image-settings-loras" aria-label={t("{displayName} 已配置 LoRA", { displayName: profile.displayName })}>
          {profile.loras.map((lora) => <li key={lora.loraId}>
            <div><strong>{lora.displayName}</strong><span className={lora.available ? '' : 'is-unavailable'}>{lora.available ? t("已就绪") : t("需要检查")}</span></div>
            {!lora.available && <p>{t(lora.message)}</p>}
          </li>)}
        </ul> : <p className="image-settings-empty">{t("尚未添加 LoRA，也可以直接使用主模型生成。")}</p>}
      </article>)}
    </div> : null}
    <details className="image-settings-advanced">
      <summary>{t("高级安装修复")}</summary>
      <p>{t("仅在需要替换或修复运行环境时使用。重新选择安装会替换当前图片配置，请填写实际适用的资料。")}</p>
      <fieldset disabled={api === undefined || busy}>
        <div className="settings-field-grid">
          <label className="settings-field"><span>{t("图片模型名称")}</span><input aria-label={t("图片模型名称")} value={modelDisplayName} maxLength={80}
            onChange={(event) => { setModelNameEdited(true); setMetadata({ ...metadata, displayName: event.currentTarget.value }) }} /></label>
          <label className="settings-field"><span>{t("图片模型 ID")}</span><input aria-label={t("图片模型 ID")} value={metadata.profileId} maxLength={64} spellCheck={false}
            onChange={(event) => { setMetadata({ ...metadata, profileId: event.currentTarget.value }) }} /></label>
          <label className="settings-field"><span>{t("模型提示词格式")}</span><select aria-label={t("模型提示词格式")} value={metadata.promptDialect}
            onChange={(event) => { setMetadata({ ...metadata, promptDialect: event.currentTarget.value as ImageProfileMetadata['promptDialect'] }) }}>
            <option value="sdxl-natural">{t("SDXL 自然语言")}</option><option value="pony-tags">{t("Pony 标签")}</option><option value="illustrious-tags">{t("Illustrious 标签")}</option>
          </select></label>
          <label className="settings-field"><span>{t("默认图片尺寸")}</span><select aria-label={t("默认图片尺寸")} value={metadata.defaultSizePresetId}
            onChange={(event) => { setMetadata({ ...metadata, defaultSizePresetId: event.currentTarget.value as ImageProfileMetadata['defaultSizePresetId'] }) }}>
            <option value="square">{t("方形 · 1024 × 1024")}</option><option value="portrait">{t("竖图 · 832 × 1216")}</option><option value="landscape">{t("横图 · 1216 × 832")}</option>
          </select></label>
          <label className="settings-field image-settings-wide"><span>{t("模型来源")}</span><input aria-label={t("模型来源")} value={metadata.checkpointSource} maxLength={512}
            onChange={(event) => { setMetadata({ ...metadata, checkpointSource: event.currentTarget.value }) }} /></label>
          <label className="settings-field image-settings-wide"><span>{t("模型许可证")}</span><input aria-label={t("模型许可证")} value={metadata.checkpointLicense} maxLength={512}
            onChange={(event) => { setMetadata({ ...metadata, checkpointLicense: event.currentTarget.value }) }} /></label>
        </div>
        <button type="button" className="secondary-button" disabled={!metadataValid} onClick={() => { void configure() }}>
          {maintenance === 'repair' ? t("正在选择与校验安装…") : t("重新选择并确认安装")}
        </button>
      </fieldset>
    </details>
    {maintenance !== null && <p role="status">{t("请完成系统选择与确认。文件校验可能需要一段时间。")}</p>}
  </section>
}
