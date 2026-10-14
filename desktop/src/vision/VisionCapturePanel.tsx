/** Review one exact display/window capture scope and preserve native ownership through cancellation and resource drain. */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { VisionState } from '../../electron/vision-protocol.ts'
import { parseVisionCaptureRegion, type VisionCaptureClient, type VisionCaptureSources,
  type VisionCaptureApproval, type VisionCaptureRegion } from '../../electron/vision-capture-protocol.ts'
import './VisionCapturePanel.css'

interface VisionCapturePanelProps {
  api: VisionCaptureClient | undefined
  chatId: string
  projectId: string | null
  ready: boolean
  disabled: boolean
  imageLimitReached: boolean
  /** Publish only a current-owner session image after the isolated capture helper actually drains. */
  onStateChange(state: VisionState): void
  /** Keep other computer actions blocked through logical cancellation and actual helper destruction. */
  onBusyChange(busy: boolean): void
}

/** Request metadata on a click, review a short-lived exact scope, and admit at most one captured frame. */
export function VisionCapturePanel({ api, chatId, projectId, ready, disabled, imageLimitReached, onStateChange, onBusyChange }: VisionCapturePanelProps) {
  const [sources, setSources] = useState<VisionCaptureSources | null>(null)
  const [selectionId, setSelectionId] = useState('')
  const [scope, setScope] = useState<'full' | 'region'>('full')
  const [percentages, setPercentages] = useState(['10', '10', '60', '60'])
  const [approval, setApproval] = useState<VisionCaptureApproval | null>(null)
  const [operation, setOperation] = useState<'list' | 'prepare' | 'capture' | 'edit' | null>(null)
  const [cancelPending, setCancelPending] = useState(false)
  const [leased, setLeased] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [clock, setClock] = useState(() => Date.now())
  const version = useRef(0)
  const physicalPending = useRef(false)
  const observedBusy = useRef(false)
  const pollPending = useRef(false)
  const report = useCallback(() => {
    const busy = physicalPending.current || observedBusy.current
    setLeased(busy)
    onBusyChange(busy)
  }, [onBusyChange])
  const observe = useCallback((busy: boolean, token: number): void => {
    if (token !== version.current) return
    observedBusy.current = busy
    report()
  }, [report])

  useEffect(() => {
    const token = ++version.current
    queueMicrotask(() => {
      if (token !== version.current) return
      setSources(null); setSelectionId(''); setApproval(null); setNotice(null); setError(null)
      if (api !== undefined) void api.getVisionCaptureState(chatId).then((next) => {
        observe(next.busy, token)
      }, () => { if (token === version.current) setError('Could not check native capture capacity. Retry when the desktop service is available.') })
    })
    return () => { version.current += 1 }
  }, [api, chatId, projectId, ready, observe])

  useEffect(() => {
    if (api === undefined || !leased) return
    const token = version.current
    // Physical-only status remains available while Backend is disconnected;
    // an inherited helper lease must not pin connection recovery after it drains.
    const timer = window.setInterval(() => {
      if (pollPending.current || token !== version.current) return
      pollPending.current = true
      void api.getVisionCaptureState(chatId).then((next) => { observe(next.busy, token) }, () => {}).finally(() => {
        pollPending.current = false
      })
    }, 300)
    return () => { window.clearInterval(timer) }
  }, [api, chatId, projectId, ready, leased, observe])

  useEffect(() => {
    if (approval === null) return
    const token = version.current
    queueMicrotask(() => { if (token === version.current) setClock(Date.now()) })
    const timer = window.setInterval(() => { setClock(Date.now()) }, 1000)
    return () => { window.clearInterval(timer) }
  }, [approval])

  function owned(owner: VisionState['owner']): boolean { return owner.chatId === chatId && owner.projectId === projectId }

  async function act(kind: 'list' | 'prepare' | 'capture' | 'edit', granted = true): Promise<void> {
    if (api === undefined || !ready || disabled || physicalPending.current || observedBusy.current
      || kind === 'prepare' && (selected === undefined || effectiveScope === 'region' && region === null)
      || kind === 'capture' && (approval === null || granted && (expired || imageLimitReached))) return
    const token = version.current, reviewed = approval
    physicalPending.current = true
    setOperation(kind); setError(null); setNotice(null)
    report()
    if (kind === 'capture') setApproval(null)
    try {
      if (kind === 'list' || kind === 'edit') {
        const next = kind === 'list' ? await api.listVisionCaptureSources(chatId) : await api.cancelVisionCapture(chatId)
        if (token !== version.current) return
        if (!owned(next.owner)) throw new Error('Capture owner changed.')
        setSources(next); setApproval(null)
        if (kind === 'list') setSelectionId('')
      } else if (kind === 'prepare') {
        const next = await api.prepareVisionCapture({ chatId, selectionId: selected!.selectionId, region: effectiveScope === 'region' ? region : null })
        if (token !== version.current) return
        if (!owned(next.owner) || next.source.selectionId !== selected?.selectionId) throw new Error('Capture scope changed.')
        setApproval(next)
      } else {
        const next = await api.captureVision({ chatId, approvalId: reviewed!.approvalId, granted })
        if (token !== version.current) return
        if (!owned(next.state.owner)) throw new Error('Capture owner changed.')
        onStateChange(next.state)
        setNotice(next.cancelled ? 'Capture cancelled. No image was added.' : 'One selected frame was added for review.')
      }
    } catch {
      if (token === version.current) {
        setApproval(null)
        setError('Capture could not complete. List sources and prepare a fresh scope before trying again.')
      }
    } finally {
      // Cancellation revokes authority immediately; the original capture call
      // settles only after its isolated stream/window has physically drained.
      physicalPending.current = false
      observedBusy.current = false
      setOperation(null)
      report()
    }
  }

  async function cancel(): Promise<void> {
    if (api === undefined || !ready || cancelPending || !leased) return
    const token = version.current
    setCancelPending(true)
    try {
      const next = await api.cancelVisionCapture(chatId)
      if (token !== version.current) return
      if (!owned(next.owner)) throw new Error('Capture owner changed.')
      setSources(next)
      observe(next.busy, token)
      setNotice(next.busy ? 'Cancellation requested. Waiting for capture resources to close.' : 'Capture cancellation requested.')
    } catch { if (token === version.current) setError('Capture cancellation could not be requested. Wait for the native action to settle.') }
    finally { setCancelPending(false) }
  }

  const visibleSources = ready && sources !== null && owned(sources.owner) ? sources.sources : []
  const selected = visibleSources.find((source) => source.selectionId === selectionId)
  const regionAllowed = selected?.kind === 'display' && selected.width !== null && selected.height !== null
  const effectiveScope = regionAllowed ? scope : 'full'
  let region: VisionCaptureRegion | null = null
  if (effectiveScope === 'region') {
    try { region = parseVisionCaptureRegion({ x: Number(percentages[0]) / 100, y: Number(percentages[1]) / 100,
      width: Number(percentages[2]) / 100, height: Number(percentages[3]) / 100 }) } catch { /* Invalid edits never reach native admission. */ }
  }
  const locked = disabled || !ready || leased || operation !== null || api === undefined
  const expired = approval !== null && clock >= Date.parse(approval.expiresAt)

  return <section className="vision-capture" aria-label="Screenshot permission" aria-busy={leased}>
    <h3>Capture a screenshot</h3>
    <p>Choose one display or window. Listing names does not read pixels; each capture needs a new confirmation.</p>
    <div className="vision-capture-controls">
      <button type="button" disabled={locked} onClick={() => { void act('list') }}>List displays and windows</button>
      <label>Capture source<select value={selected?.selectionId ?? ''} disabled={locked || approval !== null}
        onChange={(event) => { setSelectionId(event.currentTarget.value) }}>
        <option value="">Select a display or window</option>
        {visibleSources.map((source) => <option key={source.selectionId} value={source.selectionId}>{source.kind} · {source.label}</option>)}
      </select></label>
      <label>Capture scope<select value={effectiveScope} disabled={locked || approval !== null || selected === undefined}
        onChange={(event) => { setScope(event.currentTarget.value as 'full' | 'region') }}>
        <option value="full">One full selected source frame</option>
        <option value="region" disabled={!regionAllowed}>Region of selected display</option>
      </select></label>
    </div>
    {effectiveScope === 'region' && <div className="vision-capture-region">
      <p>A region capture temporarily reads one full frame of this selected display, then keeps the crop and discards pixels outside it.</p>
      {['Left (%)', 'Top (%)', 'Width (%)', 'Height (%)'].map((label, index) => <label key={label}>{label}
        <input type="number" min={index < 2 ? 0 : 0.1} max={100} step={0.1} value={percentages[index]}
          disabled={locked || approval !== null} onChange={(event) => {
            const next = event.currentTarget.value
            setPercentages((current) => current.map((value, ordinal) => ordinal === index ? next : value))
          }} />
      </label>)}
      {region === null && <p role="alert">Region percentages must stay inside this selected display.</p>}
    </div>}
    <button type="button" disabled={locked || selected === undefined || approval !== null || imageLimitReached || effectiveScope === 'region' && region === null}
      onClick={() => { void act('prepare') }}>Review capture scope</button>
    {imageLimitReached && <p>Remove an image before adding a screenshot.</p>}
    {approval !== null && <section className="vision-capture-review" aria-label="Capture scope review">
      <strong>{approval.region === null ? 'One selected source frame' : 'One selected display region'} · {approval.source.label}</strong>
      <p>Source kind: {approval.source.kind}. Expires: {approval.expiresAt}.</p>
      {approval.region !== null && <p>Normalized crop: left {approval.region.x}, top {approval.region.y}, width {approval.region.width}, height {approval.region.height}.
        One full selected-display frame is temporarily captured before the crop is kept.</p>}
      {expired && <p role="alert">This capture review expired. Edit the scope and prepare a fresh review.</p>}
      <div className="vision-capture-controls">
        <button type="button" className="tool-primary" disabled={locked || expired || imageLimitReached} onClick={() => { void act('capture') }}>Capture this source once</button>
        <button type="button" disabled={locked} onClick={() => { void act('capture', false) }}>Deny capture</button>
        <button type="button" disabled={locked} onClick={() => { void act('edit') }}>Edit capture scope</button>
      </div>
    </section>}
    {leased && <button type="button" disabled={!ready || cancelPending} onClick={() => { void cancel() }}>Cancel capture</button>}
    {leased && operation === null && <p role="status">A native capture is still active. Cancel it or wait for its resources to close.</p>}
    {operation !== null && <p role="status">{operation === 'capture' ? 'Waiting for the one-frame capture and native resources to close…' : 'Preparing capture metadata…'}</p>}
    {notice !== null && <p role="status">{notice}</p>}
    {error !== null && <p role="alert">{error}</p>}
  </section>
}
