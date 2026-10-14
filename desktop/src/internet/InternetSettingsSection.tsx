/** Edit Main-owned Internet policy without starting requests or sharing Backend settings persistence. */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  BUILTIN_INTERNET_DOMAINS,
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
  domains: string
  proxyMode: 'direct' | 'local-http'
  proxyPort: string
  timeoutSeconds: string
}

function policyDraft(state: InternetPolicyState): PolicyDraft {
  return { domains: state.allowedDomains.filter(domain => !BUILTIN_INTERNET_DOMAINS.includes(domain)).join('\n'), proxyMode: state.proxy.mode,
    proxyPort: state.proxy.mode === 'local-http' ? String(state.proxy.port) : '7890',
    timeoutSeconds: String(state.timeoutMs / 1000) }
}

function draftRequest(draft: PolicyDraft, revision: number, enabled: boolean): UpdateInternetPolicyRequest {
  return parseUpdateInternetPolicyRequest({ expectedRevision: revision, enabled,
    allowedDomains: [...new Set(draft.domains.split(/\r?\n/u).map(domain => domain.trim().toLowerCase()).filter(Boolean))],
    proxy: draft.proxyMode === 'direct' ? { mode: 'direct' } : { mode: 'local-http', port: Number(draft.proxyPort) },
    timeoutMs: Number(draft.timeoutSeconds) * 1000 })
}

/** Apply the Internet switch immediately while keeping optional connection edits in a separately reviewed draft. */
export function InternetSettingsSection({ api, onDirtyChange }: InternetSettingsSectionProps) {
  const [state, setState] = useState<InternetPolicyState | null>(null)
  const [draft, setDraft] = useState<PolicyDraft | null>(null)
  const [loading, setLoading] = useState(api !== undefined)
  const [saving, setSaving] = useState(false)
  const [enabling, setEnabling] = useState(false)
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
    try { draftRequest(draft, state.revision, state.enabled) }
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
    const request = draftRequest(draftRef.current, canonical.current.revision, canonical.current.enabled)
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

  async function enable(): Promise<void> {
    if (api === undefined || canonical.current === null || loading || saving || turningOff) return
    const current = canonical.current, token = ++operation.current
    const rebaseDraft = !conflict && baseRevision.current === current.revision
    setSaving(true); setEnabling(true); setError(null); setNotice(null)
    try {
      // The switch uses saved settings, never unsaved advanced text. A successful
      // CAS can safely rebase that unchanged draft onto our own new revision.
      const next = await api.updateInternetPolicy({ expectedRevision: current.revision, enabled: true,
        allowedDomains: [...current.allowedDomains], proxy: current.proxy, timeoutMs: current.timeoutMs })
      if (live.current && token === operation.current) {
        accept(next)
        if (rebaseDraft) { baseRevision.current = next.revision; setConflict(false) }
        setNotice('Elysia can now search when your message needs online information.')
      }
    } catch {
      if (live.current && token === operation.current) {
        setError('Internet could not be enabled. Reload preferences and try again.'); setConflict(true)
        try { const next = await api.getInternetPolicy(); if (live.current && token === operation.current) accept(next) }
        catch { /* A failed refresh cannot turn an unavailable policy into access. */ }
      }
    } finally { if (live.current) { setSaving(false); setEnabling(false) } }
  }

  async function turnOff(): Promise<void> {
    if (api === undefined || canonical.current === null || turningOff) return
    const token = ++operation.current
    setTurningOff(true); setEnabling(false); setError(null); setNotice(null)
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
      <p>Let Elysia look up current information while you chat.</p>
    </div>
    <p role="status">{state === null ? 'Internet policy is unavailable.' : `Internet is ${state.enabled ? 'on' : 'off'}.`}{notice === null ? '' : ` ${notice}`}</p>
    {api === undefined && <p>Internet settings require the desktop application.</p>}
    {state?.warning === 'storage_unavailable' && <p role="alert">Internet is off because its preferences could not be saved or read. Check local storage, then reload before enabling it.</p>}
    {error !== null && <p role="alert">{error}</p>}
    {draft === null && api !== undefined && <button type="button" className="secondary-button" disabled={loading} onClick={() => { void reload() }}>Reload Internet preferences</button>}
    {draft !== null && <>
      <div className="internet-enable-row">
        <label className="internet-enable"><input type="checkbox" checked={!turningOff && (state?.enabled === true || enabling)}
          disabled={loading || turningOff || saving && !enabling} onChange={event => { if (event.target.checked) void enable(); else void turnOff() }} />Allow Internet access</label>
        {(saving || turningOff) && <button type="button" className="secondary-button" disabled={turningOff} onClick={() => { void turnOff() }}>{turningOff ? 'Turning Internet off…' : 'Turn Internet off'}</button>}
      </div>
      <p>No API key or domain setup is needed. Web search, Wikipedia, weather, public GitHub and lyrics are included.</p>
      <details className="internet-advanced"><summary>Advanced connection settings</summary>
        <fieldset disabled={loading || saving || turningOff}>
        <label className="settings-field"><span>Allowed domains</span>
          <textarea value={draft.domains} rows={3} spellCheck={false} onChange={event => { edit({ domains: event.target.value }) }} aria-describedby="internet-domains-help" />
          <small id="internet-domains-help">Optional extra websites for manual reading. Built-in sources are already allowed. One exact domain per line; URLs, wildcards, IP addresses and local addresses are not allowed.</small>
        </label>
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
        </div>
      </details>
    </>}
    <details className="internet-privacy"><summary>Privacy and sources</summary>
    <ul className="settings-privacy-list">
      <li>Internet starts off each time Elysia opens; domain, proxy and timeout choices are saved.</li>
      <li>When your message needs online information, Elysia sends a short AI-generated search query to its search provider. It does not upload the full conversation, images, files or local memories.</li>
      <li>Built-in domains: {BUILTIN_INTERNET_DOMAINS.join(', ')}. Search results do not grant access to additional websites.</li>
      <li>Changing this policy cancels active in-app Internet requests. External browsers control their own traffic after a link opens.</li>
      <li>Local Ollama and other local model services remain available when Internet access is off.</li>
      <li>Online content is untrusted reference material. Sending, uploading, publishing, purchasing, or logging in requires separate confirmation.</li>
    </ul>
    </details>
  </section>
}
