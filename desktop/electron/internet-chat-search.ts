/** Retrieve bounded no-key web evidence for one model-selected query without following result links. */

import { isIP } from 'node:net'
import { parse, type DefaultTreeAdapterTypes } from 'parse5'
import type { InternetTransport, InternetReadResponse } from './internet-transport.js'
import { searchWikipedia } from './internet-search.js'
import type { ChatInternetSource } from './protocol.js'

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

/** Extract inert organic result titles/snippets, rejecting challenges and never fetching destination pages. */
export function parseChatWebSearch(response: InternetReadResponse): ChatInternetSource[] {
  if (response.status !== 200 || response.body.byteLength > 2 * 1024 * 1024
    || !Number.isFinite(Date.parse(response.fetchedAt)) || new Date(response.fetchedAt).toISOString() !== response.fetchedAt) {
    throw new TypeError('Web search is unavailable.')
  }
  const html = new TextDecoder('utf-8', { fatal: true }).decode(response.body)
  if (/anomaly\.js|challenge-form|anomaly-modal/iu.test(html)) throw new TypeError('Web search is unavailable.')
  const stack: DefaultTreeAdapterTypes.Node[] = [parse(html)], sources: ChatInternetSource[] = []
  const urls = new Set<string>()
  let pending: ChatInternetSource | null = null
  // Iterative inert traversal handles adversarial nesting without DOM execution
  // or recursion. Provider class names delimit organic results and their snippets.
  while (stack.length > 0) {
    const node = stack.pop()!
    if ('tagName' in node) {
      if (['script', 'style', 'template', 'iframe', 'object', 'noscript'].includes(node.tagName)) continue
      const classes = node.attrs.find(attribute => attribute.name === 'class')?.value.split(/\s+/u) ?? []
      if (node.tagName === 'a' && classes.includes('result-link')) {
        pending = null
        const url = resultUrl(node.attrs.find(attribute => attribute.name === 'href')?.value ?? '')
        const title = textContent(node, 2048)
        if (url && title && !urls.has(url) && sources.length < 5) {
          pending = { title, url, snippet: '', fetchedAt: response.fetchedAt, provider: 'web' }
        }
      } else if (classes.includes('result-snippet') && pending) {
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

/** Search one fixed web endpoint, with one encyclopedia fallback and a shared cancellation deadline. */
export async function searchChatInternet(transport: Pick<InternetTransport, 'read'>,
  request: { chatId: string; query: string; language: 'en' | 'zh' }, signal: AbortSignal): Promise<ChatInternetSource[]> {
  if (!request.query.trim() || Buffer.byteLength(request.query) > 2048 || [...request.query].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
    || !['en', 'zh'].includes(request.language)) throw new TypeError('Search query is invalid.')
  const deadline = new AbortController(), cancel = (): void => deadline.abort()
  signal.addEventListener('abort', cancel, { once: true })
  if (signal.aborted) cancel()
  const timer = setTimeout(cancel, 45000)
  try {
    const url = new URL('https://lite.duckduckgo.com/lite/')
    url.searchParams.set('q', request.query)
    try {
      const response = await transport.read(url.href, { accept: 'text/html, text/plain', maxBytes: 2 * 1024 * 1024,
        maxRedirects: 0, signal: deadline.signal })
      if (response.url !== url.href) throw new TypeError('Search origin changed.')
      const sources = parseChatWebSearch(response)
      if (sources.length > 0 && !deadline.signal.aborted) return sources
    } catch { if (deadline.signal.aborted) throw new Error('Search cancelled.') }
    if (deadline.signal.aborted) throw new Error('Search cancelled.')
    const fallback = await searchWikipedia(transport, { chatId: request.chatId, query: request.query, language: request.language }, deadline.signal)
    if (deadline.signal.aborted) throw new Error('Search cancelled.')
    return fallback.sources.map(({ title, url, snippet, fetchedAt }) => ({ title, url, snippet, fetchedAt, provider: 'wikipedia' }))
  } finally { clearTimeout(timer); signal.removeEventListener('abort', cancel) }
}
