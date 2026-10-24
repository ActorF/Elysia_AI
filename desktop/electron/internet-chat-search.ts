/** Retrieve bounded no-key search evidence and inert page excerpts only from domains already allowed by the user. */

import { isIP } from 'node:net'
import { parse, type DefaultTreeAdapterTypes } from 'parse5'
import type { InternetTransport, InternetReadResponse } from './internet-transport.js'
import { searchWikipedia } from './internet-search.js'
import { readInternetPage } from './internet-reader.js'
import type { ChatInternetSource } from './protocol.js'

/** Stable search failures distinguish blocked providers and malformed evidence without exposing remote bodies. */
export type InternetChatSearchFailure = 'unavailable' | 'challenge' | 'invalid_response' | 'no_results'
/** Keep both failed provider attempts visible to Main without exposing request text or native diagnostics. */
export class InternetChatSearchError extends Error {
  /** Record safe primary/fallback categories so an empty answer cannot hide a failed live lookup. */
  constructor(readonly primaryCode: InternetChatSearchFailure, readonly fallbackCode?: InternetChatSearchFailure, options?: ErrorOptions) {
    super(`Chat Internet search failed: ${primaryCode}${fallbackCode ? `; fallback: ${fallbackCode}` : ''}.`, options)
  }
}

function bounded(value: string, maximum: number): string {
  let result = '', size = 0
  for (const character of value) {
    const bytes = Buffer.byteLength(character)
    if (size + bytes > maximum) break
    result += character; size += bytes
  }
  return result
}

function textContent(root: DefaultTreeAdapterTypes.Node, maximum: number): string {
  const stack = [root], pieces: string[] = []
  while (stack.length > 0) {
    const node = stack.pop()!
    if ('tagName' in node && ['script', 'style', 'template', 'iframe', 'object', 'noscript'].includes(node.tagName)) continue
    if ('value' in node && node.nodeName === '#text') pieces.push(node.value)
    else if ('childNodes' in node) for (let index = node.childNodes.length - 1; index >= 0; index--) stack.push(node.childNodes[index]!)
  }
  let result = '', size = 0
  const plain = [...pieces.join(' ')].map(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127 ? ' ' : character).join('')
  for (const character of plain.replace(/\s+/gu, ' ').trim()) {
    const bytes = Buffer.byteLength(character)
    if (size + bytes > maximum) break
    result += character; size += bytes
  }
  return result
}

function resultUrl(value: string): string | null {
  try {
    let url = new URL(value, 'https://lite.duckduckgo.com')
    // Decode the provider's wrapper locally. Following it would introduce a
    // second network authority and leak a click the user never requested.
    if (url.hostname === 'duckduckgo.com' && url.pathname === '/l/') url = new URL(url.searchParams.get('uddg') ?? '')
    if (url.protocol !== 'https:' || url.username || url.password || url.port || isIP(url.hostname)
      || url.hostname.startsWith('[') || !url.hostname.includes('.') || /(?:\.|^)(?:localhost|local|internal|test|invalid)$/iu.test(url.hostname)
      || url.hostname.endsWith('.') || url.hostname.endsWith('duckduckgo.com') || Buffer.byteLength(url.href) > 4096) return null
    url.hash = ''
    return url.href
  } catch { return null }
}

/** Extract inert organic result titles/snippets, rejecting challenges and malformed responses without fetching pages. */
export function parseChatWebSearch(response: InternetReadResponse): ChatInternetSource[] {
  if (response.status !== 200) throw new InternetChatSearchError('unavailable')
  if (!(response.body instanceof Uint8Array) || response.body.byteLength > 2 * 1024 * 1024
    || !Number.isFinite(Date.parse(response.fetchedAt)) || new Date(response.fetchedAt).toISOString() !== response.fetchedAt) {
    throw new InternetChatSearchError('invalid_response')
  }
  let html: string
  try { html = new TextDecoder('utf-8', { fatal: true }).decode(response.body) }
  catch { throw new InternetChatSearchError('invalid_response') }
  if (/anomaly\.js|challenge-form|anomaly-modal/iu.test(html)) throw new InternetChatSearchError('challenge')
  const stack: DefaultTreeAdapterTypes.Node[] = [parse(html)], sources: ChatInternetSource[] = []
  const urls = new Set<string>()
  let pending: ChatInternetSource | null = null, visited = 0
  // Iterative inert traversal handles adversarial nesting without DOM execution
  // or recursion. Provider class names delimit organic results and their snippets.
  while (stack.length > 0) {
    const node = stack.pop()!
    if (++visited > 100_000) throw new InternetChatSearchError('invalid_response')
    if ('tagName' in node) {
      if (['script', 'style', 'template', 'iframe', 'object', 'noscript'].includes(node.tagName)) continue
      const classes = node.attrs.find(attribute => attribute.name === 'class')?.value.split(/\s+/u) ?? []
      if (node.tagName === 'a' && (classes.includes('result-link') || classes.includes('result__a'))) {
        pending = null
        const url = resultUrl(node.attrs.find(attribute => attribute.name === 'href')?.value ?? '')
        const title = textContent(node, 2048)
        if (url && title && !urls.has(url) && sources.length < 5) {
          pending = { title, url, snippet: '', fetchedAt: response.fetchedAt, provider: 'web' }
        }
      } else if ((classes.includes('result-snippet') || classes.includes('result__snippet')) && pending) {
        pending.snippet = textContent(node, 4096)
        if (pending.snippet) { sources.push(pending); urls.add(pending.url) }
        pending = null
      }
    }
    if ('childNodes' in node) for (let index = node.childNodes.length - 1; index >= 0; index--) stack.push(node.childNodes[index]!)
  }
  while (Buffer.byteLength(JSON.stringify(sources)) > 32768) sources.pop()
  return sources
}

async function readAllowedPages(transport: Pick<InternetTransport, 'read'>, sources: ChatInternetSource[],
  domains: readonly string[], language: 'en' | 'zh', signal: AbortSignal, onReading?: () => void): Promise<ChatInternetSource[]> {
  const selected = sources.filter(source => domains.includes(new URL(source.url).hostname)).slice(0, 2)
  if (selected.length > 0) onReading?.()
  const excerpts = new Map<string, ChatInternetSource>()
  // Each page uses the same policy-controlled transport. This prefilter avoids
  // pointless denied requests; it never grants a new host or authorizes redirects.
  for (const source of selected) {
    if (signal.aborted) throw new Error('Search cancelled.')
    try {
      const page = await readInternetPage(transport, source.url, signal)
      if (signal.aborted) throw new Error('Search cancelled.')
      const prefix = language === 'zh' ? '网页正文摘录（节选，并非全文）：' : 'Page text excerpt (partial, not the full page): '
      excerpts.set(source.url, { ...source, title: page.title, url: page.url, fetchedAt: page.fetchedAt, snippet: bounded(`${prefix}${page.content}`, 4096) })
    } catch { if (signal.aborted) throw new Error('Search cancelled.') }
  }
  const prefix = language === 'zh' ? '搜索结果摘要（未读取网页正文）：' : 'Search result snippet (page body was not read): '
  const seen = new Set<string>()
  const enriched = sources.map(source => excerpts.get(source.url) ?? { ...source, snippet: bounded(`${prefix}${source.snippet}`, 4096) })
    .filter(source => { if (seen.has(source.url)) return false; seen.add(source.url); return true })
  while (Buffer.byteLength(JSON.stringify(enriched)) > 32768) enriched.pop()
  return enriched
}

/** Search one fixed endpoint, read up to two already permitted pages, and retain honest fallback/error provenance. */
export async function searchChatInternet(transport: Pick<InternetTransport, 'read'>,
  request: { chatId: string; query: string; language: 'en' | 'zh'; allowedDomains?: readonly string[] }, signal: AbortSignal,
  onReading?: () => void): Promise<ChatInternetSource[]> {
  if (!request.query.trim() || Buffer.byteLength(request.query) > 2048 || [...request.query].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
    || !['en', 'zh'].includes(request.language)) throw new TypeError('Search query is invalid.')
  const deadline = new AbortController(), cancel = (): void => deadline.abort()
  signal.addEventListener('abort', cancel, { once: true })
  if (signal.aborted) cancel()
  const timer = setTimeout(cancel, 45000)
  try {
    if (deadline.signal.aborted) throw new Error('Search cancelled.')
    const url = new URL('https://lite.duckduckgo.com/lite/')
    url.searchParams.set('q', request.query)
    let primary: InternetChatSearchFailure = 'unavailable'
    try {
      const response = await transport.read(url.href, { accept: 'text/html, text/plain', maxBytes: 2 * 1024 * 1024,
        maxRedirects: 0, signal: deadline.signal })
      if (response.url !== url.href) throw new InternetChatSearchError('invalid_response')
      const sources = parseChatWebSearch(response)
      if (sources.length > 0 && !deadline.signal.aborted) return await readAllowedPages(transport, sources,
        request.allowedDomains ?? [], request.language, deadline.signal, onReading)
      primary = 'no_results'
    } catch (error) {
      if (deadline.signal.aborted) throw new Error('Search cancelled.', { cause: error })
      primary = error instanceof InternetChatSearchError ? error.primaryCode : 'unavailable'
    }
    if (deadline.signal.aborted) throw new Error('Search cancelled.')
    try {
      const fallback = await searchWikipedia(transport, { chatId: request.chatId, query: request.query, language: request.language }, deadline.signal)
      if (deadline.signal.aborted) throw new Error('Search cancelled.')
      if (fallback.sources.length === 0) throw new InternetChatSearchError(primary, 'no_results')
      const prefix = request.language === 'zh'
        ? `网页搜索未取得可用结果（${primary}）；以下仅为维基百科摘要，无法据此核实现时信息：`
        : `Web search returned no usable evidence (${primary}); this is only a Wikipedia excerpt and does not verify live information: `
      return fallback.sources.map(({ title, url, snippet, fetchedAt }) => ({ title, url,
        snippet: bounded(`${prefix}${snippet}`, 4096), fetchedAt, provider: 'wikipedia' }))
    } catch (error) {
      if (deadline.signal.aborted) throw new Error('Search cancelled.', { cause: error })
      throw error instanceof InternetChatSearchError ? error : new InternetChatSearchError(primary,
        error instanceof TypeError ? 'invalid_response' : 'unavailable', { cause: error })
    }
  } finally { clearTimeout(timer); signal.removeEventListener('abort', cancel) }
}
