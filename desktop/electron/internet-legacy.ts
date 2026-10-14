/** Apply the Internet policy to existing lyrics retrieval and explicit browser handoffs without widening their native authority. */

import { InternetPolicyService } from './internet-policy.js'
import { InternetTransport, parseInternetUrl } from './internet-transport.js'
import { SongLyricsProvider } from './song-lyrics-provider.js'
import { SongLyricsAssetPreparer, type SongLyricsAssetPreparationInput,
  type SongLyricsAssetPreparationResult } from './song-cover-lyrics-assets.js'

/** Check lyrics permission before showing a picker; this performs no lookup or network probe. */
export function assertInternetLyricsAllowed(policy: InternetPolicyService): void {
  const lease = policy.acquire()
  try { parseInternetUrl('https://lrclib.net/api/get', lease.policy) }
  finally { lease.release() }
}

/** Prepare the existing private lyrics assets under one revocable permission, including probe, retry waits, and file writes. */
export async function prepareInternetLyrics(policy: InternetPolicyService, transport: InternetTransport,
  input: SongLyricsAssetPreparationInput): Promise<SongLyricsAssetPreparationResult> {
  const lease = policy.acquire(input.signal)
  try {
    parseInternetUrl('https://lrclib.net/api/get', lease.policy)
    const provider = new SongLyricsProvider(async (request, signal) => {
      lease.assertCurrent()
      if (request.origin !== 'https://lrclib.net' || request.method !== 'GET'
        || !/^\/api\/(?:get|search)\?/u.test(request.path)) throw new Error('Lyrics request is invalid.')
      // Preserve the lyrics adapter's stricter deadline and no-redirect rule.
      // A policy change also aborts its retry delay, not just the current HTTP call.
      const response = await transport.read(request.origin + request.path, {
        accept: 'application/json', maxBytes: request.maxResponseBytes, maxRedirects: 0,
        signal: AbortSignal.any([lease.signal, signal, AbortSignal.timeout(request.timeoutMs)]),
      })
      return { statusCode: response.status, headers: response.headers, body: response.body }
    })
    const preparer = new SongLyricsAssetPreparer((query, signal) => provider.lookup(query, signal))
    const result = await preparer.prepare({ ...input, signal: lease.signal })
    lease.assertCurrent()
    return result
  } finally { lease.release() }
}

/** Confirm one public HTTPS URL before handing it to the user's independently managed browser. */
export async function openInternetBrowser(policy: InternetPolicyService, value: string,
  confirm: (url: string) => Promise<boolean>, open: (url: string) => Promise<void>): Promise<void> {
  const lease = policy.acquire()
  try {
    const hostname = new URL(value).hostname
    if (!hostname.includes('.') || /(?:\.|^)(?:localhost|local|internal|test|invalid)$/iu.test(hostname)) throw new Error('Browser URL is invalid.')
    // A deliberate browser click may open a search citation without editing
    // native fetch permissions. This temporary validation view grants no HTTP
    // transport access and never changes the stored domain policy.
    const url = parseInternetUrl(value, { ...lease.policy, allowedDomains: [hostname] }).href
    if (!await confirm(url)) return
    // The permission must still be current after the native dialog yields.
    // The browser controls its own cookies and later requests; this lease only
    // authorizes the handoff and cannot revoke a tab already opened by the user.
    lease.assertCurrent()
    await open(url)
  } finally { lease.release() }
}
