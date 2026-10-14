/** Edit Main-owned Internet policy without starting requests or sharing Backend settings persistence. */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  parseUpdateInternetPolicyRequest,
  type InternetPolicyClient,
  type InternetPolicyState,
  type UpdateInternetPolicyRequest,
} from '../../electron/internet-policy-protocol.ts'
import './InternetSettingsSection.css'

interface InternetSettingsSectionProps {
  api: InternetPolicyClient | undefined
  /** Include the independent policy draft in the Settings navigation guard. */
  onDirtyChange(dirty: boolean): void
}

interface PolicyDraft {
  enabled: boolean
  domains: string
  proxyMode: 'direct' | 'local-http'
  proxyPort: string
  timeoutSeconds: string
}

const DOMAIN_PRESETS = [
  { label: 'Wikipedia', domains: ['en.wikipedia.org', 'zh.wikipedia.org'] },
  { label: 'GitHub', domains: ['api.github.com', 'github.com'] },
  { label: 'Open-Meteo', domains: ['api.open-meteo.com', 'geocoding-api.open-meteo.com'] },
  { label: 'Lyrics', domains: ['lrclib.net'] },
] as const

function policyDraft(state: InternetPolicyState): PolicyDraft {
  return { enabled: state.enabled, domains: state.allowedDomains.join('\n'), proxyMode: state.proxy.mode,
    proxyPort: state.proxy.mode === 'local-http' ? String(state.proxy.port) : '7890',
    timeoutSeconds: String(state.timeoutMs / 1000) }
}

function draftRequest(draft: PolicyDraft, revision: number): UpdateInternetPolicyRequest {
  return parseUpdateInternetPolicyRequest({ expectedRevision: revision, enabled: draft.enabled,
    allowedDomains: [...new Set(draft.domains.split(/\r?\n/u).map(domain => domain.trim().toLowerCase()).filter(Boolean))],
    proxy: draft.proxyMode === 'direct' ? { mode: 'direct' } : { mode: 'local-http', port: Number(draft.proxyPort) },
    timeoutMs: Number(draft.timeoutSeconds) * 1000 })
}

/** Keep Internet off until an explicit revision-checked save, with an independent immediate off control. */
export function InternetSettingsSection({ api, onDirtyChange }: InternetSettingsSectionProps) {
  const [state, setState] = useState<InternetPolicyState | null>(null)
  const [draft, setDraft] = useState<PolicyDraft | null>(null)
  const [loading, setLoading] = useState(api !== undefined)
  const [saving, setSaving] = useState(false)
  const [turningOff, setTurningOff] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const canonical = useRef<InternetPolicyState | null>(null)
  const draftRef = useRef<PolicyDraft | null>(null)
  const baseRevision = useRef<number | null>(null)
  const live = useRef(false)
  const operation = useRef(0)
  const dirty = draft !== null && state !== null && JSON.stringify(draft) !== JSON.stringify(policyDraft(state))
  let validationError: string | null = null
  if (draft !== null && state !== null) {
    try { draftRequest(draft, state.revision) }
    catch { validationError = 'Use up to 32 exact domain names, a timeout from 1 to 60 seconds, and a proxy port from 1 to 65535.' }
  }

  useLayoutEffect(() => { onDirtyChange(dirty); return () => { onDirtyChange(false) } }, [dirty, onDirtyChange])

  const accept = useCallback((next: InternetPolicyState, replaceDraft = false): void => {
    if (!live.current || canonical.current !== null && next.revision < canonical.current.revision) return
    const previous = canonical.current
    const edited = previous !== null && draftRef.current !== null
      && JSON.stringify(draftRef.current) !== JSON.stringify(policyDraft(previous))
    canonical.current = next
    setState(next)
    if (replaceDraft || !edited) {
      const nextDraft = policyDraft(next)
      draftRef.current = nextDraft; baseRevision.current = next.revision
      setDraft(nextDraft); setConflict(false)
    } else if (next.revision !== baseRevision.current) setConflict(true)
  }, [])

  useEffect(() => {
    live.current = true
    let events = 0
    const unsubscribe = api?.onInternetPolicyChanged((next) => { events += 1; accept(next) })
    // Revocation can change enabled without advancing the persistent revision.
    // An initial read captured before that event must never restore stale intent.
    void api?.getInternetPolicy().then((next) => { if (events === 0) accept(next) }, () => {
      if (live.current) setError('Internet preferences could not be loaded. Internet access remains unavailable.')
    }).finally(() => { if (live.current) setLoading(false) })
    return () => { live.current = false; operation.current += 1; unsubscribe?.() }
  }, [accept, api])

  function edit(values: Partial<PolicyDraft>): void {
    if (draftRef.current === null) return
    const next = { ...draftRef.current, ...values }
    draftRef.current = next; setDraft(next); setNotice(null)
  }

  async function reload(): Promise<void> {
    if (api === undefined || saving || turningOff) return
    const token = ++operation.current
    setLoading(true); setError(null); setNotice(null)
    try {
      const next = await api.getInternetPolicy()
      if (live.current && token === operation.current) accept(next, true)
    } catch { if (live.current && token === operation.current) setError('Could not reload Internet preferences. Try again.') }
    finally { if (live.current && token === operation.current) setLoading(false) }
  }

  async function save(): Promise<void> {
    if (api === undefined || draftRef.current === null || canonical.current === null || saving || turningOff || conflict) return
    const request = draftRequest(draftRef.current, canonical.current.revision)
    const token = ++operation.current
    setSaving(true); setError(null); setNotice(null)
    try {
      const next = await api.updateInternetPolicy(request)
      if (live.current && token === operation.current) { accept(next, true); setNotice('Internet preferences saved.') }
    } catch {
      if (live.current && token === operation.current) {
        setError('Internet preferences could not be saved. Reload the current policy before trying again.')
        setConflict(true)
        try {
          const next = await api.getInternetPolicy()
          if (live.current && token === operation.current) accept(next)
        } catch { /* Retain the draft for recovery when local storage is unavailable. */ }
      }
    } finally { if (live.current) setSaving(false) }
  }

  async function turnOff(): Promise<void> {
    if (api === undefined || canonical.current === null || turningOff) return
    const token = ++operation.current
    setTurningOff(true); setError(null); setNotice(null)
    try {
      let current = canonical.current
      let next: InternetPolicyState
      try {
        next = await api.updateInternetPolicy({ expectedRevision: current.revision, enabled: false,
          allowedDomains: [...current.allowedDomains], proxy: current.proxy, timeoutMs: current.timeoutMs })
      } catch {
        // A simultaneous save can advance the revision. The user's explicit
        // off intent still applies, so retry once against canonical local state.
        current = await api.getInternetPolicy()
        next = await api.updateInternetPolicy({ expectedRevision: current.revision, enabled: false,
          allowedDomains: [...current.allowedDomains], proxy: current.proxy, timeoutMs: current.timeoutMs })
      }
      if (live.current && token === operation.current) { accept(next, true); setNotice('Internet is off. In-app requests have been cancelled.') }
    } catch {
      if (live.current && token === operation.current) {
        setError('Could not save the off preference. Reload to check the current state before restarting Elysia.')
        try {
          const next = await api.getInternetPolicy()
          if (live.current && token === operation.current) accept(next, true)
        } catch { /* Report unavailable canonical state without claiming an off result. */ }
      }
    } finally { if (live.current) setTurningOff(false) }
  }

  return <section className="settings-section internet-settings" aria-labelledby="internet-settings-heading" aria-busy={loading || saving || turningOff}
    onKeyDown={event => {
      // This section sits in the existing global settings form. Enter in an
      // Internet field must not implicitly submit unrelated Backend preferences.
      if (event.key === 'Enter' && event.target instanceof HTMLInputElement) event.preventDefault()
    }}>
    <div className="settings-section-heading">
      <h2 id="internet-settings-heading">Internet access</h2>
      <p>Choose which public services Elysia may contact when you request online information.</p>
    </div>
    <p role="status">{state === null ? 'Internet policy is unavailable.' : `Internet is ${state.enabled ? 'on' : 'off'}.`}{notice === null ? '' : ` ${notice}`}</p>
    {api === undefined && <p>Internet settings require the desktop application.</p>}
    {state?.warning === 'storage_unavailable' && <p role="alert">Internet is off because its preferences could not be saved or read. Check local storage, then reload before enabling it.</p>}
    {error !== null && <p role="alert">{error}</p>}
    {draft === null && api !== undefined && <button type="button" className="secondary-button" disabled={loading} onClick={() => { void reload() }}>Reload Internet preferences</button>}
    {draft !== null && <>
      <fieldset disabled={loading || saving || turningOff}>
        <label className="internet-enable"><input type="checkbox" checked={draft.enabled} onChange={event => { edit({ enabled: event.target.checked }) }} />Allow Internet access</label>
        <p>The switch takes effect when you save. An empty domain list allows no services.</p>
        <label className="settings-field"><span>Allowed domains</span>
          <textarea value={draft.domains} rows={5} spellCheck={false} onChange={event => { edit({ domains: event.target.value }) }} aria-describedby="internet-domains-help" />
          <small id="internet-domains-help">One exact domain per line. Subdomains need their own entry; URLs, wildcards, IP addresses, and local addresses are not allowed.</small>
        </label>
        <div className="internet-preset-actions" aria-label="Domain presets">
          {DOMAIN_PRESETS.map(preset => <button key={preset.label} type="button" className="secondary-button" onClick={() => {
            const existing = draft.domains.split(/\r?\n/u).map(domain => domain.trim().toLowerCase()).filter(Boolean)
            edit({ domains: [...new Set([...existing, ...preset.domains])].join('\n') })
          }}>Add {preset.label}</button>)}
        </div>
        <div className="settings-field-grid">
          <label className="settings-field"><span>Connection</span><select value={draft.proxyMode} onChange={event => { edit({ proxyMode: event.target.value as PolicyDraft['proxyMode'] }) }}>
            <option value="direct">Direct connection</option><option value="local-http">Local HTTP proxy</option>
          </select></label>
          {draft.proxyMode === 'local-http' && <label className="settings-field"><span>Proxy port at 127.0.0.1</span><input type="number" min="1" max="65535" step="1" value={draft.proxyPort} onChange={event => { edit({ proxyPort: event.target.value }) }} /></label>}
          <label className="settings-field"><span>Request timeout (seconds)</span><input type="number" min="1" max="60" step="0.001" value={draft.timeoutSeconds} onChange={event => { edit({ timeoutSeconds: event.target.value }) }} /></label>
        </div>
        <p>Elysia ignores system and environment proxy settings. A local HTTP proxy uses 127.0.0.1 and the port above; if it fails, Elysia does not fall back to a direct connection.</p>
      </fieldset>
      {validationError !== null && <p role="alert">{validationError}</p>}
      {conflict && <p role="alert">The active policy changed or a save failed. Reload to review the current policy before saving this draft.</p>}
      <div className="internet-policy-actions">
        <button type="button" className="primary-button" disabled={!dirty || loading || saving || turningOff || conflict || validationError !== null} onClick={() => { void save() }}>{saving ? 'Saving Internet preferences…' : 'Save Internet preferences'}</button>
        <button type="button" className="secondary-button" disabled={loading || saving || turningOff} onClick={() => { void reload() }}>Reload Internet preferences</button>
        <button type="button" className="secondary-button" disabled={turningOff} onClick={() => { void turnOff() }}>{turningOff ? 'Turning Internet off…' : 'Turn Internet off'}</button>
      </div>
    </>}
    <ul className="settings-privacy-list">
      <li>Internet starts off each time Elysia opens; domain, proxy and timeout choices are saved.</li>
      <li>Only requests you start send queries or selected service inputs to allowed domains. Chat messages, images, files, and local memories are not automatically uploaded.</li>
      <li>Changing this policy cancels active in-app Internet requests. External browsers control their own traffic after a link opens.</li>
      <li>Local Ollama and other local model services remain available when Internet access is off.</li>
      <li>Online content is untrusted reference material. Sending, uploading, publishing, purchasing, or logging in requires separate confirmation.</li>
    </ul>
  </section>
}
