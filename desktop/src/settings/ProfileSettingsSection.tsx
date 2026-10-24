/** Edit the canonical personal name independently of model settings and preserve drafts across reload failures. */

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { DesktopApi, UserProfileState } from '../../electron/contracts.ts'
import { codePointLength } from '../../electron/protocol-text.ts'
import { HelpHint } from '../design-system/HelpHint.tsx'
import { t, useLocale } from '../i18n/LocaleProvider.tsx'
import { formatUiError } from '../i18n/ui-error.ts'

interface ProfileSettingsSectionProps {
  api?: Pick<DesktopApi, 'getProfile' | 'updateProfile'>
  generationBusy: boolean
  restartPending: boolean
  /** Include this independently saved draft in the Settings navigation guard. */
  onDirtyChange(dirty: boolean): void
}

function nameError(value: string): string | null {
  const normalized = value.trim()
  if (!normalized) return 'Enter a name before saving.'
  if (codePointLength(normalized) > 80) return 'Use 80 characters or fewer for your name.'
  // The Unicode flag preserves valid surrogate pairs while rejecting lone
  // surrogates, controls and line separators consistently with the native API.
  if (/[\p{Cc}\p{Cs}\u2028\u2029]/u.test(normalized)) {
    return 'Your name must be a single line without control characters.'
  }
  return null
}

/** Save a compare-and-set profile name without submitting or resetting the global settings form. */
export function ProfileSettingsSection({ api, generationBusy, restartPending, onDirtyChange }: ProfileSettingsSectionProps) {
  useLocale()
  const id = useId()
  const [profile, setProfile] = useState<UserProfileState | null>(null)
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [operationKind, setOperationKind] = useState<'load' | 'save'>('load')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const operation = useRef(0)
  const busy = useRef(false)
  const dirty = profile !== null && draft !== profile.userName
  const validationError = dirty ? nameError(draft) : null
  const available = typeof api?.getProfile === 'function' && typeof api?.updateProfile === 'function'

  const reload = useCallback(async (): Promise<void> => {
    if (busy.current) return
    if (typeof api?.getProfile !== 'function') {
      setError('Your name is unavailable. Reload and try again.')
      return
    }
    const token = ++operation.current
    busy.current = true
    setPending(true)
    setOperationKind('load')
    setError(null)
    setSaved(false)
    try {
      const next = await api.getProfile()
      if (token !== operation.current) return
      setProfile(next)
      setDraft(next.userName)
    } catch (failure) {
      if (token === operation.current) setError(failure instanceof Error ? failure.message : 'Your name could not be loaded or saved. Reload and try again.')
    } finally {
      if (token === operation.current) { busy.current = false; setPending(false) }
    }
  }, [api])

  useEffect(() => {
    const token = ++operation.current
    queueMicrotask(() => { if (token === operation.current) void reload() })
    return () => {
      // IPC may settle after navigation or a Backend restart. A retired
      // component must not consume its result or clear the new owner's lease.
      operation.current += 1
      busy.current = false
    }
  }, [reload])
  useLayoutEffect(() => {
    onDirtyChange(dirty)
    return () => { onDirtyChange(false) }
  }, [dirty, onDirtyChange])

  async function save(): Promise<void> {
    if (!available || api === undefined || profile === null || busy.current || generationBusy || restartPending || !dirty) return
    const invalid = nameError(draft)
    if (invalid !== null) { setError(invalid); return }
    const token = ++operation.current
    busy.current = true
    setPending(true)
    setOperationKind('save')
    setError(null)
    setSaved(false)
    try {
      // The exact loaded value is the compare-and-set token, including legacy
      // empty names. Trimming applies only to the proposed replacement.
      const next = await api.updateProfile({ expectedUserName: profile.userName, userName: draft.trim() })
      if (token !== operation.current) return
      setProfile(next)
      setDraft(next.userName)
      setSaved(true)
    } catch (failure) {
      if (token === operation.current) setError(failure instanceof Error ? failure.message : 'Your name could not be loaded or saved. Reload and try again.')
    } finally {
      if (token === operation.current) { busy.current = false; setPending(false) }
    }
  }

  return <section className="settings-section" aria-labelledby={`${id}-heading`}>
    <div className="settings-section-heading">
      <h2 id={`${id}-heading`}>{t('Your name')}</h2>
      <HelpHint id={`${id}-help`} label={t('Your name')}>
        {t('Elysia uses this name from your next reply. Your previous conversations stay unchanged.')}
      </HelpHint>
    </div>
    <div className="settings-field-grid">
      <label className="settings-field">
        <span className="visually-hidden">{t('Your name')}</span>
        <input type="text" value={draft} autoComplete="nickname" spellCheck={false}
          aria-labelledby={`${id}-heading`}
          disabled={!available || profile === null || pending} aria-describedby={`${id}-help`}
          aria-invalid={validationError !== null} aria-errormessage={validationError === null ? undefined : `${id}-validation`}
          onChange={event => { setDraft(event.target.value); setError(null); setSaved(false) }}
          onKeyDown={event => {
            if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
            event.preventDefault(); event.stopPropagation(); void save()
          }} />
        {validationError !== null && <small id={`${id}-validation`} className="settings-field-error">{t(validationError)}</small>}
      </label>
    </div>
    {error !== null && <p role="alert" className="settings-field-error">{formatUiError(t(error))}</p>}
    {pending && <p role="status">{t(operationKind === 'load' ? 'Loading your name…' : 'Saving…')}</p>}
    {saved && <p role="status">{t('Name saved.')}</p>}
    {dirty && generationBusy && <p role="status">{t('Wait for the current reply before changing your name.')}</p>}
    <div className="settings-save-actions">
      <button type="button" className="primary-button" onClick={() => { void save() }}
        disabled={!available || profile === null || pending || generationBusy || restartPending || !dirty || validationError !== null}>
        {t('Save name')}
      </button>
      <button type="button" className="secondary-button" onClick={() => { void reload() }}
        disabled={!available || pending || restartPending}>{t('Reload saved name')}</button>
    </div>
  </section>
}
