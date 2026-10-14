/**
 * Isolate one approved Electron display/window frame in a short-lived sandbox helper.
 *
 * Source discovery requests zero thumbnails and no icons. The helper has its own
 * nonpersistent session, private preload, exact local document, closed network
 * allowlist, and single frame/user-gesture/source-bound display-media grant.
 * The static helper can request only display media: generic media permission
 * needed by Chromium is confined to this exact desktop-only helper/job, while
 * microphone, loopback audio, downloads, navigation, and other frames stay
 * denied. A request settles only after the helper WebContents is destroyed,
 * so cancelled media cannot overlap a newly admitted Vision action.
 */

import { createHash, randomUUID } from 'node:crypto'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { BrowserWindow, desktopCapturer, ipcMain, screen, session,
  type IpcMainEvent, type IpcMainInvokeEvent, type DesktopCapturerSource } from 'electron'
import { validateVisionDimensions, validateVisionFileName } from './vision-protocol.js'
import { parseVisionCaptureRegion, type VisionCaptureRegion } from './vision-capture-protocol.js'
import { VisionCaptureError, type NativeVisionCaptureSource, type NativeVisionCaptureFrame } from './vision-capture.js'

/** Select only trusted app-owned helper artifacts; this configuration is never exposed through renderer IPC. */
export interface VisionCaptureNativeOptions { helperDocumentPath?: string; preloadPath?: string; timeoutMs?: number }
/** Return private native callbacks for injection into the capture owner controller. */
export interface VisionCaptureNativeAdapter {
  /** Enumerate metadata without thumbnails, application icons, audio, or capture streams. */
  listSources(): Promise<NativeVisionCaptureSource[]>
  /** Capture one source under the current controller grant and retain capacity until helper destruction. */
  captureFrame(source: NativeVisionCaptureSource, region: VisionCaptureRegion | null, signal: AbortSignal,
    stillValid: () => boolean): Promise<NativeVisionCaptureFrame>
}

function digest(value: unknown): string { return createHash('sha256').update(JSON.stringify(value)).digest('hex') }
// One reusable nonpersistent Session avoids accumulating a new Electron browser
// context per screenshot. This module lease also excludes overlapping adapters
// while a previous helper is draining after a lifecycle/controller replacement.
let nativeLease = false
function safeLabel(name: string, kind: 'display' | 'window', index: number): string {
  const fallback = `${kind === 'display' ? 'Display' : 'Window'} ${index + 1}`
  // Paths in window titles are not needed to select a capture and must not
  // become retained provenance. Such titles receive an anonymous local label.
  if (typeof name !== 'string' || /[\\/]/u.test(name)) return fallback
  const clean = Array.from(name).filter(character => {
    const point = character.codePointAt(0)!
    return point >= 32 && point !== 127 && !(point >= 0xd800 && point <= 0xdfff)
      && !/[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/u.test(character)
  }).join('').replace(/[<>:"|?*]/gu, ' ').trim().replace(/[.\s]+$/u, '')
  const bounded = Array.from(clean).slice(0, 255).join('').trim().replace(/[.\s]+$/u, '')
  try { return validateVisionFileName(bounded) } catch { return fallback }
}
function metadata(source: DesktopCapturerSource, index: number): NativeVisionCaptureSource {
  const kind = source.id.startsWith('screen:') ? 'display' : 'window'
  const display = kind === 'display' ? screen.getAllDisplays().find(item => String(item.id) === source.display_id) : undefined
  const width = display === undefined ? null : Math.round(display.size.width * display.scaleFactor)
  const height = display === undefined ? null : Math.round(display.size.height * display.scaleFactor)
  if (width !== null && (!Number.isSafeInteger(width) || !Number.isSafeInteger(height)
    || width < 1 || height! < 1 || width > 32768 || height! > 32768)) throw new VisionCaptureError('unavailable')
  return { nativeId: source.id, kind, label: safeLabel(source.name, kind, index), width, height,
    identity: digest({ id: source.id, kind, name: source.name, displayId: source.display_id,
      display: display === undefined ? null : { id: display.id, bounds: display.bounds,
        size: display.size, scaleFactor: display.scaleFactor, rotation: display.rotation } }) }
}
async function enumerate(): Promise<{ native: DesktopCapturerSource; source: NativeVisionCaptureSource }[]> {
  const sources = await desktopCapturer.getSources({ types: ['screen', 'window'], thumbnailSize: { width: 0, height: 0 }, fetchWindowIcons: false })
  if (sources.length > 128) throw new VisionCaptureError('limit_exceeded')
  return sources.map((source, index) => ({ native: source, source: metadata(source, index) }))
}

/** Construct inert fixed native callbacks; no desktop source enumeration or capture occurs until an explicit controller operation. */
export function createVisionCaptureNative(options: VisionCaptureNativeOptions = {}): VisionCaptureNativeAdapter {
  const packaged = fileURLToPath(new URL('../dist/vision-capture.html', import.meta.url))
  const documentPath = options.helperDocumentPath ?? (existsSync(packaged) ? packaged : fileURLToPath(new URL('../public/vision-capture.html', import.meta.url)))
  const preloadPath = options.preloadPath ?? fileURLToPath(new URL('./vision-capture-preload.cjs', import.meta.url))
  const timeoutMs = options.timeoutMs ?? 20000
  if (!path.isAbsolute(documentPath) || !path.isAbsolute(preloadPath) || !Number.isSafeInteger(timeoutMs)
    || timeoutMs < 1 || timeoutMs > 20000) throw new TypeError('Vision capture native configuration is invalid.')

  return {
    listSources: async () => (await enumerate()).map(item => item.source),
    captureFrame: (selected, region, signal, stillValid) => {
      if (nativeLease) return Promise.reject(new VisionCaptureError('busy'))
      if (signal.aborted || !stillValid()) return Promise.reject(new VisionCaptureError('cancelled'))
      const checkedRegion = parseVisionCaptureRegion(region)
      const job = randomUUID().replaceAll('-', '')
      const prefix = `vision-capture-private:${job}:`
      const documentUrl = pathToFileURL(documentPath).href
      const scriptUrl = pathToFileURL(path.join(path.dirname(documentPath), 'vision-capture-frame.js')).href
      nativeLease = true
      let privateSession: Electron.Session, helper: BrowserWindow
      try {
        privateSession = session.fromPartition('elysia-vision-capture', { cache: false })
        helper = new BrowserWindow({ show: false, width: 1, height: 1, skipTaskbar: true,
        webPreferences: { session: privateSession, preload: preloadPath, additionalArguments: [`--elysia-vision-capture-job=${job}`],
          sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false,
          backgroundThrottling: false, spellcheck: false } })
      } catch { nativeLease = false; return Promise.reject(new VisionCaptureError('unavailable')) }
      const contents = helper.webContents
      return new Promise<NativeVisionCaptureFrame>((resolve, reject) => {
        let mediaClaimed = false, frameClaimed = false, configurationClaimed = false, permissionClaimed = false
        let result: NativeVisionCaptureFrame | null = null
        let failure: VisionCaptureError | null = null
        let finishing = false, destroyed = false
        const deadline = performance.now() + timeoutMs
        const trustedFrame = (event: IpcMainEvent | IpcMainInvokeEvent): boolean => !helper.isDestroyed()
          && event.sender === contents && event.senderFrame === contents.mainFrame
          && event.senderFrame?.url === documentUrl
        const valid = (): boolean => !finishing && !signal.aborted && performance.now() < deadline && stillValid()
        const configuration = (event: IpcMainEvent): void => {
          if (!trustedFrame(event) || !valid() || configurationClaimed) { event.returnValue = null; return }
          configurationClaimed = true
          event.returnValue = { region: checkedRegion }
        }
        const onDownload = (event: Electron.Event): void => event.preventDefault()
        const cleanup = (): void => {
          clearTimeout(timer); signal.removeEventListener('abort', onAbort)
          ipcMain.removeListener(`${prefix}configuration`, configuration)
          ipcMain.removeHandler(`${prefix}finish`); ipcMain.removeHandler(`${prefix}failure`)
          privateSession.setDisplayMediaRequestHandler((_request, callback) => callback({}))
          privateSession.setPermissionCheckHandler(() => false)
          privateSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
          privateSession.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (_details, callback) => callback({ cancel: true }))
          privateSession.removeListener('will-download', onDownload)
        }
        const settled = (): void => {
          if (destroyed) return
          destroyed = true
          try { cleanup() } catch { failure ??= new VisionCaptureError('unavailable') }
          if (failure !== null) reject(failure)
          else if (result === null) reject(new VisionCaptureError('unavailable'))
          else resolve(result)
        }
        const finish = (error: VisionCaptureError | null): void => {
          if (finishing) return
          finishing = true; failure = error
          try { if (!helper.isDestroyed()) helper.destroy() }
          catch { failure ??= new VisionCaptureError('unavailable') }
          // In Electron, WebContents destruction is the physical stream owner
          // boundary. Neither a cancellation request nor window close intent
          // alone releases this Promise while Chromium could retain the tracks.
          if (contents.isDestroyed()) settled()
        }
        const onAbort = (): void => finish(new VisionCaptureError('cancelled'))
        const timer = setTimeout(() => finish(new VisionCaptureError('timeout')), timeoutMs)
        try {
        contents.once('destroyed', settled)
        contents.once('render-process-gone', () => finish(new VisionCaptureError('unavailable')))
        contents.once('preload-error', () => finish(new VisionCaptureError('unavailable')))
        helper.on('closed', () => { if (!finishing) finish(new VisionCaptureError('cancelled')) })
        contents.setWindowOpenHandler(() => ({ action: 'deny' }))
        contents.on('will-navigate', (event, url) => { if (url !== documentUrl) { event.preventDefault(); finish(new VisionCaptureError('unavailable')) } })
        contents.on('will-attach-webview', event => event.preventDefault())
        privateSession.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (details, callback) => {
          callback({ cancel: details.url !== documentUrl && details.url !== scriptUrl })
        })
        privateSession.on('will-download', onDownload)
        privateSession.setPermissionCheckHandler((requestContents, permission, _origin, details) => {
          // Electron's current request-handler union includes display-capture;
          // its check-handler type declaration omits that runtime category.
          return valid() && (permission as string) === 'display-capture' && requestContents === contents
            && details.isMainFrame && contents.mainFrame.url === documentUrl
        })
        privateSession.setPermissionRequestHandler((requestContents, permission, callback, details) => {
          const mainFrame = 'isMainFrame' in details && details.isMainFrame === true
          const mediaTypes = 'mediaTypes' in details ? details.mediaTypes : null
          // Electron 43/Chromium requests `media` for getDisplayMedia before its
          // display handler. Denying that actual category makes the approved
          // helper unusable. Allow it once only in this static, private frame,
          // after job configuration. In the tested Electron 43 runtime desktop
          // media has an empty mediaTypes array; camera and microphone requests
          // explicitly list video/audio and remain denied. The display handler
          // separately verifies video=true and audio=false before granting.
          const allowed = valid() && !permissionClaimed && configurationClaimed && requestContents === contents && mainFrame
            && contents.mainFrame.url === documentUrl && details.requestingUrl === documentUrl
            && (permission === 'display-capture' || permission === 'media' && Array.isArray(mediaTypes) && mediaTypes.length === 0)
          if (allowed) permissionClaimed = true
          callback(allowed)
        })
        privateSession.setDisplayMediaRequestHandler((request, callback) => {
          const respond = (streams: Electron.Streams): void => {
            try { callback(streams) } catch { finish(new VisionCaptureError('unavailable')) }
          }
          if (!valid() || !configurationClaimed || !permissionClaimed || mediaClaimed || request.frame !== contents.mainFrame || request.frame?.url !== documentUrl
            || !request.userGesture || !request.videoRequested || request.audioRequested
            || !['file://', 'file:///'].includes(request.securityOrigin)) {
            respond({}); return
          }
          mediaClaimed = true
          void enumerate().then(sources => {
            const current = sources.find(item => item.source.nativeId === selected.nativeId && item.source.identity === selected.identity)
            if (!valid() || current === undefined) { respond({}); finish(new VisionCaptureError('owner_changed')); return }
            respond({ video: current.native })
          }).catch(() => { respond({}); finish(new VisionCaptureError('unavailable')) })
        }, { useSystemPicker: false })
        ipcMain.on(`${prefix}configuration`, configuration)
        ipcMain.handle(`${prefix}finish`, (event, value: unknown) => {
          if (!trustedFrame(event) || !valid() || !mediaClaimed || !configurationClaimed || frameClaimed) throw new VisionCaptureError('permission_denied')
          frameClaimed = true
          try {
            if (value === null || typeof value !== 'object' || Array.isArray(value)
              || Object.keys(value).sort().join(',') !== 'bytes,height,width') throw new VisionCaptureError('invalid_image')
            const frame = value as { bytes: unknown; width: unknown; height: unknown }
            if (!(frame.bytes instanceof Uint8Array) || frame.bytes.byteLength < 45 || frame.bytes.byteLength > 16 * 1024 * 1024) {
              throw new VisionCaptureError('invalid_image')
            }
            const dimensions = validateVisionDimensions(frame.width, frame.height)
            result = { pngBytes: Buffer.from(frame.bytes), ...dimensions }
            finish(null)
            return null
          } catch { finish(new VisionCaptureError('invalid_image')); return null }
        })
        ipcMain.handle(`${prefix}failure`, event => {
          if (!trustedFrame(event) || !valid()) throw new VisionCaptureError('permission_denied')
          finish(new VisionCaptureError('unavailable')); return null
        })
        signal.addEventListener('abort', onAbort, { once: true })
        if (signal.aborted) onAbort()
        else void helper.loadFile(documentPath).then(async () => {
          if (!valid()) { finish(new VisionCaptureError('cancelled')); return }
          const available = await contents.executeJavaScript('isSecureContext && typeof navigator.mediaDevices?.getDisplayMedia === "function" && typeof globalThis.elysiaCaptureStart === "function" && typeof globalThis.elysiaPrivateVisionCapture === "object"')
          if (!available || !valid()) { finish(new VisionCaptureError('unavailable')); return }
          // This native gesture exists only after the controller consumed the
          // user's one-use decision. No unapproved source or periodic timer can
          // invoke the helper's browser capture function.
          await contents.executeJavaScript('globalThis.elysiaCaptureStart()', true)
        }).catch(() => finish(new VisionCaptureError('unavailable')))
        } catch { finish(new VisionCaptureError('unavailable')) }
      }).finally(() => { nativeLease = false })
    },
  }
}
