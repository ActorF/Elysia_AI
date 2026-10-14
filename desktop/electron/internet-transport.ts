/** Perform bounded public HTTPS reads and one closed GitHub Issue write with pinned DNS and physically drained cancellation. */

import { Resolver } from 'node:dns/promises'
import { request as httpRequest, type ClientRequest, type IncomingMessage } from 'node:http'
import { request as httpsRequest, type RequestOptions } from 'node:https'
import { isIP, type Socket } from 'node:net'
import { connect as tlsConnect, type TLSSocket } from 'node:tls'
import type { InternetPolicyState } from './internet-policy-protocol.js'
import { InternetPolicyError, InternetPolicyService, type InternetPolicyLease } from './internet-policy.js'

/** Fixed transport failures never include server response bodies or private network addresses. */
export type InternetTransportErrorCode = 'invalid_url' | 'domain_denied' | 'private_address' | 'dns_failed'
  | 'cancelled' | 'timeout' | 'network_failed' | 'invalid_response' | 'limit_exceeded' | 'redirect_denied'
/** Publish a bounded category without exposing native diagnostics to Renderer. */
export class InternetTransportError extends Error {
  /** Attach a fixed safe transport diagnostic. */
  constructor(readonly code: InternetTransportErrorCode) { super(`Internet request: ${code}.`) }
}
/** Native callers select only a supported response format and a tighter response budget. */
export interface InternetReadOptions {
  readonly accept: 'application/json' | 'text/html, text/plain'
  readonly maxBytes: number
  readonly signal?: AbortSignal
  readonly maxRedirects?: 0 | 3
}
/** Bytes and provenance remain detached from executable browser documents. */
export interface InternetReadResponse {
  readonly url: string
  readonly status: number
  readonly headers: Readonly<Record<string, string>>
  readonly body: Uint8Array
  readonly fetchedAt: string
}
/** Main supplies the exact independently reviewed Issue payload; credentials are never part of this transferable object. */
export interface GithubIssuePayload {
  readonly owner: string
  readonly repo: string
  readonly title: string
  readonly body: string
}
/** Distinguish confirmed creation from rejection, no attempted transmission, and an outcome that must not be retried blindly. */
export interface GithubIssueTransportResult {
  readonly outcome: 'created' | 'rejected' | 'unknown' | 'not_sent'
  readonly url: string | null
  readonly number: number | null
}
interface GithubIssueTransmission {
  readonly body: Buffer
  readonly token: string
  readonly onAttempt: () => void
  readonly onComplete: (response: InternetReadResponse) => void
}
/** Trusted native tunnel parameters never originate in provider response bodies. */
export interface InternetProxyTarget {
  readonly port: number
  readonly address: string
  readonly hostname: string
  readonly signal: AbortSignal
}
/** Test injections retain native completion semantics and never cross the Renderer bridge. */
export interface InternetTransportNative {
  /** Return every candidate address and settle only after the resolver has stopped. */
  resolve(hostname: string, signal: AbortSignal): Promise<readonly string[]>
  /** Open a pinned HTTPS request using the supplied fixed headers and TLS options. */
  request(options: RequestOptions, onResponse: (response: IncomingMessage) => void): ClientRequest
  /** Connect through the explicitly configured loopback proxy and verify target TLS before returning. */
  proxy(target: InternetProxyTarget): Promise<TLSSocket>
}
/** Main may replace native operations for deterministic tests; production uses Node's verified TLS stack. */
export interface InternetTransportOptions { readonly native?: Partial<InternetTransportNative> }

function fail(code: InternetTransportErrorCode): never { throw new InternetTransportError(code) }

function githubIssuePayload(value: GithubIssuePayload, token: string): GithubIssuePayload {
  if (typeof value !== 'object' || value === null || Array.isArray(value) || Reflect.ownKeys(value).length !== 4
    || !['owner', 'repo', 'title', 'body'].every(key => Object.hasOwn(value, key))
    || typeof value.owner !== 'string' || !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/u.test(value.owner)
    || typeof value.repo !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/u.test(value.repo) || value.repo === '.' || value.repo === '..'
    || typeof token !== 'string' || !/^[\x21-\x7e]{1,4096}$/u.test(token)) fail('invalid_response')
  for (const [content, maximum, body] of [[value.title, 256, false], [value.body, 4000, true]] as const) {
    if (typeof content !== 'string' || !body && content.trim() === '' || Buffer.byteLength(content, 'utf8') > maximum
      || [...content].some(character => {
        const point = character.codePointAt(0)!
        return point < 32 && (!body || !'\t\n\r'.includes(character)) || point === 127 || point >= 0xd800 && point <= 0xdfff
      })) fail('invalid_response')
  }
  return { owner: value.owner, repo: value.repo, title: value.title, body: value.body }
}

function createdGithubIssue(response: InternetReadResponse, payload: GithubIssuePayload): GithubIssueTransportResult {
  const data: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(response.body))
  if (typeof data !== 'object' || data === null || Array.isArray(data)) fail('invalid_response')
  const value = data as Record<string, unknown>
  if (!Number.isSafeInteger(value.number) || (value.number as number) <= 0 || typeof value.html_url !== 'string'
    || value.html_url.length > 512 || value.pull_request !== undefined) fail('invalid_response')
  const url = new URL(value.html_url)
  if (url.href !== value.html_url || url.origin !== 'https://github.com' || url.username !== '' || url.password !== ''
    || url.search !== '' || url.hash !== ''
    || url.pathname.toLowerCase() !== `/${payload.owner}/${payload.repo}/issues/${value.number as number}`.toLowerCase()) fail('invalid_response')
  return { outcome: 'created', url: url.href, number: value.number as number }
}

/** Validate exact HTTPS origin permission without DNS, sockets, browser navigation or credential handling. */
export function parseInternetUrl(value: string, policy: InternetPolicyState): URL {
  if (typeof value !== 'string' || value.length > 4096 || /[\s\\]/u.test(value)
    || [...value].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) fail('invalid_url')
  let url: URL
  try { url = new URL(value) } catch { return fail('invalid_url') }
  if (url.protocol !== 'https:' || url.port !== '' && url.port !== '443' || url.username !== '' || url.password !== ''
    || url.hash !== '' || isIP(url.hostname) !== 0 || url.hostname.startsWith('[') || url.hostname.endsWith('.')) fail('invalid_url')
  if (!policy.allowedDomains.includes(url.hostname)) fail('domain_denied')
  return url
}

/** Admit globally routable unicast addresses while rejecting local, reserved, transition and documentation blocks. */
export function isPublicInternetAddress(value: string): boolean {
  if (isIP(value) === 4) {
    const [a, b, c] = value.split('.').map(Number) as [number, number, number, number]
    return !(a === 0 || a === 10 || a === 127 || a >= 224 || a === 100 && b >= 64 && b <= 127
      || a === 169 && b === 254 || a === 172 && b >= 16 && b <= 31
      || a === 192 && (b === 168 || b === 0 && (c === 0 || c === 2) || b === 88 && c === 99)
      || a === 198 && (b === 18 || b === 19 || b === 51 && c === 100) || a === 203 && b === 0 && c === 113)
  }
  if (isIP(value) !== 6 || value.includes('.') || value.includes('%')) return false
  const halves = value.toLowerCase().split('::')
  const left = halves[0] === '' ? [] : halves[0]!.split(':'), right = halves.length === 1 || halves[1] === '' ? [] : halves[1]!.split(':')
  const words = [...left, ...Array<string>(8 - left.length - right.length).fill('0'), ...right].map(word => Number.parseInt(word, 16))
  // Accept only global unicast 2000::/3. IPv4-mapped/NAT64/6to4 and special
  // 2001::/23 blocks are deliberately excluded instead of attempting tunnelling.
  return (words[0]! & 0xe000) === 0x2000 && words[0] !== 0x2002
    && !(words[0] === 0x2001 && (words[1]! < 0x0200 || words[1] === 0x0db8))
    && !(words[0] === 0x3fff && words[1]! < 0x1000)
}

async function resolvePublic(hostname: string, signal: AbortSignal): Promise<readonly string[]> {
  if (signal.aborted) fail('cancelled')
  const resolver = new Resolver({ timeout: 5000, tries: 1 })
  const cancel = (): void => { resolver.cancel() }
  signal.addEventListener('abort', cancel, { once: true })
  try {
    const results = await Promise.allSettled([resolver.resolve4(hostname), resolver.resolve6(hostname)])
    if (signal.aborted) fail('cancelled')
    const addresses = results.flatMap(result => result.status === 'fulfilled' ? result.value : [])
    if (addresses.length === 0) fail('dns_failed')
    return [...new Set(addresses)]
  } finally { signal.removeEventListener('abort', cancel) }
}

function openProxy(target: InternetProxyTarget): Promise<TLSSocket> {
  return new Promise((resolve, reject) => {
    let request: ClientRequest | null = null, raw: Socket | null = null, secure: TLSSocket | null = null
    let delivered = false, failure: InternetTransportError | null = null, requestClosed = false
    let rawClosed = true, secureClosed = true
    const finish = (): void => {
      if (delivered || failure === null || !requestClosed || !rawClosed || !secureClosed) return
      delivered = true; target.signal.removeEventListener('abort', abort); reject(failure)
    }
    const stop = (code: InternetTransportErrorCode): void => {
      failure ??= new InternetTransportError(code)
      secure?.destroy(); raw?.destroy(); request?.destroy(); finish()
    }
    const abort = (): void => stop('cancelled')
    target.signal.addEventListener('abort', abort, { once: true })
    try {
      const authority = isIP(target.address) === 6 ? `[${target.address}]:443` : `${target.address}:443`
      request = httpRequest({ host: '127.0.0.1', port: target.port, method: 'CONNECT', path: authority,
        agent: false, maxHeaderSize: 16384, headers: { Host: authority, Connection: 'close' } })
      request.on('socket', socket => {
        raw = socket; rawClosed = false
        socket.once('close', () => { rawClosed = true; finish() })
      })
      request.once('error', () => stop('network_failed'))
      request.once('close', () => { requestClosed = true; finish() })
      request.once('response', response => { response.destroy(); stop('network_failed') })
      request.once('connect', (response, socket, head) => {
        if (failure !== null || target.signal.aborted || response.statusCode !== 200 || head.byteLength !== 0) {
          socket.destroy(); stop(target.signal.aborted ? 'cancelled' : 'network_failed'); return
        }
        secureClosed = false
        secure = tlsConnect({ socket, servername: target.hostname, rejectUnauthorized: true, minVersion: 'TLSv1.2' })
        secure.once('error', () => stop('network_failed'))
        secure.once('close', () => { secureClosed = true; finish() })
        secure.once('secureConnect', () => {
          if (failure !== null || target.signal.aborted) { stop('cancelled'); return }
          delivered = true; target.signal.removeEventListener('abort', abort); resolve(secure!)
        })
      })
      if (target.signal.aborted) stop('cancelled')
      else request.end()
    } catch { requestClosed = request === null; stop('network_failed') }
  })
}

/** Own bounded reads and a fixed approved Issue write without automatic probes, retries, cookies or environment proxies. */
export class InternetTransport {
  private readonly native: InternetTransportNative
  /** Construct inert native adapters; the service is consulted for every explicit read. */
  constructor(private readonly service: InternetPolicyService, options: InternetTransportOptions = {}) {
    this.native = { resolve: resolvePublic, request: httpsRequest, proxy: openProxy, ...options.native }
  }

  /** Read one authorized URL, validating each redirect anew under one total deadline and one physical policy lease. */
  async read(value: string, options: InternetReadOptions): Promise<InternetReadResponse> {
    if (!['application/json', 'text/html, text/plain'].includes(options.accept) || !Number.isSafeInteger(options.maxBytes)
      || options.maxBytes < 1 || options.maxBytes > 2 * 1024 * 1024
      || options.maxRedirects !== undefined && options.maxRedirects !== 0 && options.maxRedirects !== 3) fail('invalid_response')
    const lease = this.service.acquire(options.signal), abort = new AbortController()
    let timedOut = false
    const cancel = (): void => { abort.abort() }
    lease.signal.addEventListener('abort', cancel, { once: true })
    const timer = setTimeout(() => { timedOut = true; abort.abort() }, lease.policy.timeoutMs)
    try {
      let url = parseInternetUrl(value, lease.policy)
      const visited = new Set<string>()
      for (let redirects = 0; ; redirects += 1) {
        lease.assertCurrent()
        if (abort.signal.aborted) fail(timedOut ? 'timeout' : 'cancelled')
        if (visited.has(url.href)) fail('redirect_denied')
        visited.add(url.href)
        const addresses = await this.native.resolve(url.hostname, abort.signal)
        lease.assertCurrent()
        if (addresses.length === 0 || addresses.length > 64) fail('dns_failed')
        if (addresses.some(address => !isPublicInternetAddress(address))) fail('private_address')
        if (abort.signal.aborted) fail(timedOut ? 'timeout' : 'cancelled')
        const response = await this.single(url, addresses[0]!, lease.policy, options, abort.signal)
        lease.assertCurrent()
        if (abort.signal.aborted) fail(timedOut ? 'timeout' : 'cancelled')
        if (![301, 302, 303, 307, 308].includes(response.status)) return response
        if (redirects >= (options.maxRedirects ?? 3) || response.headers.location === undefined) fail('redirect_denied')
        let next: URL
        try { next = new URL(response.headers.location, url) } catch { return fail('invalid_url') }
        url = parseInternetUrl(next.href, lease.policy)
      }
    } catch (error) {
      if (timedOut) fail('timeout')
      if (error instanceof InternetTransportError || error instanceof InternetPolicyError) throw error
      if (abort.signal.aborted) fail('cancelled')
      fail('network_failed')
    } finally { clearTimeout(timer); lease.signal.removeEventListener('abort', cancel); lease.release() }
  }

  /**
   * Send exactly one independently approved GitHub Issue creation using a Main-only token.
   *
   * No destination, method, header or redirect is caller-selectable. Once end()
   * is attempted, cancellation before a complete validated reply, malformed
   * replies and server failures have an unknown outcome. A known creation or
   * rejection survives cancellation during drain: it cannot undo the write. This method
   * never retries and returns only after DNS, TLS and response ownership drains.
   */
  async createGithubIssue(value: GithubIssuePayload, token: string, signal?: AbortSignal): Promise<GithubIssueTransportResult> {
    let attempted = false, lease: InternetPolicyLease | null = null
    let knownResult: GithubIssueTransportResult | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    const abort = new AbortController(), cancel = (): void => { abort.abort() }
    const result = (outcome: GithubIssueTransportResult['outcome']): GithubIssueTransportResult => ({ outcome, url: null, number: null })
    try {
      const payload = githubIssuePayload(value, token)
      lease = this.service.acquire(signal)
      lease.signal.addEventListener('abort', cancel, { once: true })
      timer = setTimeout(cancel, lease.policy.timeoutMs)
      const url = parseInternetUrl(`https://api.github.com/repos/${payload.owner}/${payload.repo}/issues`, lease.policy)
      lease.assertCurrent()
      const addresses = await this.native.resolve(url.hostname, abort.signal)
      lease.assertCurrent()
      if (abort.signal.aborted) fail('cancelled')
      if (addresses.length === 0 || addresses.length > 64) fail('dns_failed')
      if (addresses.some(address => !isPublicInternetAddress(address))) fail('private_address')
      const response = await this.single(url, addresses[0]!, lease.policy,
        { accept: 'application/json', maxBytes: 128 * 1024, maxRedirects: 0 }, abort.signal,
        { body: Buffer.from(JSON.stringify({ title: payload.title, body: payload.body }), 'utf8'), token,
          onAttempt: () => { attempted = true }, onComplete: completed => {
            // A validated complete reply establishes the external outcome even
            // if permission is revoked while its socket is still draining.
            // Cancellation cannot undo a creation already confirmed by GitHub.
            if (completed.status === 201) knownResult = createdGithubIssue(completed, payload)
            else if (completed.status >= 400 && completed.status < 500) knownResult = result('rejected')
          } })
      lease.assertCurrent()
      if (abort.signal.aborted) fail('cancelled')
      if (response.status === 201) return createdGithubIssue(response, payload)
      // A complete client-error response explicitly rejects this creation.
      // Redirects and unexpected/server statuses remain uncertain and are
      // never followed, especially with an Authorization header present.
      return result(response.status >= 400 && response.status < 500 ? 'rejected' : 'unknown')
    } catch { return knownResult ?? result(attempted ? 'unknown' : 'not_sent') }
    finally {
      if (timer !== undefined) clearTimeout(timer)
      lease?.signal.removeEventListener('abort', cancel)
      lease?.release()
    }
  }

  private async single(url: URL, address: string, policy: InternetPolicyState, options: InternetReadOptions,
    signal: AbortSignal, write?: GithubIssueTransmission): Promise<InternetReadResponse> {
    let tunnel: TLSSocket | undefined
    if (policy.proxy.mode === 'local-http') {
      tunnel = await this.native.proxy({ port: policy.proxy.port, address, hostname: url.hostname, signal })
      if (signal.aborted) {
        await new Promise<void>(resolve => { if (tunnel!.closed) resolve(); else { tunnel!.once('close', resolve); tunnel!.destroy() } })
        fail('cancelled')
      }
    }
    return new Promise((resolve, reject) => {
      let outgoing: ClientRequest | null = null, incoming: IncomingMessage | null = null, socket: Socket | TLSSocket | null = tunnel ?? null
      let requestClosed = false, responseClosed = false, socketClosed = tunnel === undefined, ended = false, settled = false
      let failure: InternetTransportError | null = null, size = 0
      const chunks: Buffer[] = []
      const responseData = (): InternetReadResponse => {
        const headers: Record<string, string> = {}
        for (const key of ['content-type', 'content-length', 'content-encoding', 'location', 'last-modified', 'etag', 'date', 'retry-after']) {
          const value = incoming!.headers[key]
          if (typeof value === 'string') headers[key] = value
        }
        return { url: url.href, status: incoming!.statusCode!, headers, body: Buffer.concat(chunks), fetchedAt: new Date().toISOString() }
      }
      const finish = (): void => {
        if (settled || !requestClosed || incoming !== null && !responseClosed || !socketClosed) return
        settled = true; signal.removeEventListener('abort', onAbort)
        if (failure !== null) { reject(failure); return }
        if (!ended || incoming === null) { reject(new InternetTransportError('network_failed')); return }
        resolve(responseData())
      }
      const stop = (code: InternetTransportErrorCode): void => {
        failure ??= new InternetTransportError(code); incoming?.destroy(); outgoing?.destroy(); socket?.destroy(); finish()
      }
      const watchSocket = (value: Socket | TLSSocket): void => {
        socket = value; socketClosed = value.closed
        value.once('close', () => { socketClosed = true; finish() })
      }
      if (tunnel !== undefined) watchSocket(tunnel)
      const onAbort = (): void => stop('cancelled')
      signal.addEventListener('abort', onAbort, { once: true })
      try {
        // Connect to the vetted literal directly: no second DNS lookup or
        // resolver fallback can replace it. Host/SNI retain the original name
        // for HTTP routing and certificate verification.
        outgoing = this.native.request({ hostname: address, port: 443, servername: url.hostname,
          path: `${url.pathname}${url.search}`, method: write === undefined ? 'GET' : 'POST', agent: tunnel === undefined ? false : undefined, maxHeaderSize: 16384,
          rejectUnauthorized: true, minVersion: 'TLSv1.2',
          ...(tunnel === undefined ? {} : { createConnection: () => tunnel! }),
          headers: { Host: url.hostname, Accept: options.accept, 'Accept-Encoding': 'identity', Connection: 'close',
            'User-Agent': 'Elysia/0.1.0 (https://github.com/ActorF/Elysia_AI)',
            ...(write === undefined ? {} : { Accept: 'application/vnd.github+json', Authorization: `Bearer ${write.token}`,
              'X-GitHub-Api-Version': '2026-03-10', 'Content-Type': 'application/json', 'Content-Length': write.body.byteLength }) } }, response => {
          incoming = response
          response.once('close', () => { responseClosed = true; finish() })
          response.once('error', () => stop('network_failed'))
          response.once('aborted', () => stop('network_failed'))
          response.on('data', (chunk: Buffer) => {
            size += chunk.byteLength
            if (size > options.maxBytes) stop('limit_exceeded')
            else if (failure === null) chunks.push(Buffer.from(chunk))
          })
          response.once('end', () => {
            ended = true
            if (write !== undefined && failure === null) {
              // Capture only a fully received, bounded write reply. A GET still
              // rejects cancellation at any point before physical completion.
              try { write.onComplete(responseData()) } catch { stop('invalid_response'); return }
            }
            finish()
          })
          const length = response.headers['content-length'], encoding = response.headers['content-encoding']
          const contentType = response.headers['content-type']
          if (response.statusCode === undefined || response.statusCode < 200 || response.statusCode > 599
            || encoding !== undefined && encoding !== 'identity') stop('invalid_response')
          else if (length !== undefined && (!/^\d+$/u.test(length) || Number(length) > options.maxBytes)) stop('limit_exceeded')
          else if (response.statusCode >= 200 && response.statusCode < 300
            && (typeof contentType !== 'string' || !(options.accept === 'application/json'
              ? /^application\/(?:[a-z0-9.+-]+\+)?json(?:\s*;|$)/iu
              : /^text\/(?:html|plain)(?:\s*;|$)/iu).test(contentType))) stop('invalid_response')
        })
        outgoing.once('socket', watchSocket)
        outgoing.once('error', () => stop('network_failed'))
        outgoing.once('close', () => { requestClosed = true; finish() })
        if (signal.aborted) stop('cancelled')
        else { write?.onAttempt(); outgoing.end(write?.body) }
      } catch { requestClosed = outgoing === null; stop('network_failed') }
    })
  }
}
