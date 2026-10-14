/** Fetch explicitly requested English or Chinese Wikipedia search results with bounded inert snippets and direct provenance. */

import { randomUUID } from 'node:crypto'
import { parseFragment, type DefaultTreeAdapterTypes } from 'parse5'
import type { InternetTransport } from './internet-transport.js'
import { parseInternetSearchRequest, parseInternetSource, type InternetSearchRequest, type InternetSource } from './internet-protocol.js'

/** Return no more than five encyclopedia sources from one physical bounded HTTPS request. */
export interface InternetSearchResult { readonly sources: readonly InternetSource[]; readonly fetchedAt: string }

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new TypeError('Wikipedia response is invalid.')
  return value as Record<string, unknown>
}
function boundedText(value: unknown, maximum: number): string {
  if (typeof value !== 'string' || Buffer.byteLength(value, 'utf8') > maximum) throw new TypeError('Wikipedia response text is invalid.')
  return value
}
function plainSnippet(value: unknown): { text: string; truncated: boolean } {
  const raw = boundedText(value, 16384)
  // parse5 builds inert syntax nodes and decodes the full HTML entity set. It
  // creates no browser document and cannot fetch or execute embedded resources.
  // Iterative traversal avoids stack growth from deeply nested remote markup.
  const stack: DefaultTreeAdapterTypes.Node[] = [parseFragment(raw)]
  let plain = ''
  while (stack.length > 0) {
    const node = stack.pop()!
    if ('tagName' in node && ['script', 'style', 'template', 'iframe', 'object', 'noscript'].includes(node.tagName)) continue
    if (node.nodeName === '#text' && 'value' in node) plain += node.value
    else if ('childNodes' in node) {
      if ('tagName' in node && ['br', 'div', 'p', 'li'].includes(node.tagName)) plain += ' '
      for (let index = node.childNodes.length - 1; index >= 0; index -= 1) stack.push(node.childNodes[index]!)
    }
  }
  plain = plain.replace(/\s+/gu, ' ').trim()
  let result = '', bytes = 0
  for (const character of plain) {
    const size = Buffer.byteLength(character, 'utf8')
    if (bytes + size > 2048) return { text: result, truncated: true }
    result += character; bytes += size
  }
  return { text: result, truncated: false }
}

/** Normalize one search response; Wikipedia timestamp denotes last edit and never supplies a publication date. */
export function parseWikipediaSearchResult(value: unknown, language: 'en' | 'zh', fetchedAt: string): InternetSearchResult {
  if (!['en', 'zh'].includes(language) || !Number.isFinite(Date.parse(fetchedAt)) || new Date(fetchedAt).toISOString() !== fetchedAt) {
    throw new TypeError('Wikipedia provenance is invalid.')
  }
  const root = object(value)
  if (root.error !== undefined || root.warnings !== undefined) throw new TypeError('Wikipedia search was unavailable.')
  const query = object(root.query)
  if (!Array.isArray(query.search) || query.search.length > 5) throw new TypeError('Wikipedia search results are invalid.')
  const pageIds = new Set<number>()
  const sources = query.search.map(raw => {
    const entry = object(raw)
    if (!Number.isSafeInteger(entry.pageid) || (entry.pageid as number) <= 0 || entry.ns !== 0 || pageIds.has(entry.pageid as number)) {
      throw new TypeError('Wikipedia search identity is invalid.')
    }
    pageIds.add(entry.pageid as number)
    const title = boundedText(entry.title, 2048), snippet = plainSnippet(entry.snippet)
    const timestamp = boundedText(entry.timestamp, 64), modified = new Date(timestamp)
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u.test(timestamp) || !Number.isFinite(modified.getTime())
      || modified.toISOString() !== (timestamp.includes('.') ? timestamp : timestamp.replace('Z', '.000Z'))) {
      throw new TypeError('Wikipedia modification time is invalid.')
    }
    return parseInternetSource({ id: `internet_${randomUUID().replaceAll('-', '')}`, title,
      url: `https://${language}.wikipedia.org/?curid=${entry.pageid as number}`, snippet: snippet.text, content: null,
      publishedAt: null, modifiedAt: modified.toISOString(), fetchedAt, provider: 'wikipedia', fromCache: false, truncated: snippet.truncated })
  })
  return { sources, fetchedAt }
}

/** Perform one user-requested read with fixed provider, namespace, result count and response budget; never paginate automatically. */
export async function searchWikipedia(transport: Pick<InternetTransport, 'read'>, value: InternetSearchRequest,
  signal: AbortSignal): Promise<InternetSearchResult> {
  const request = parseInternetSearchRequest(value)
  const url = new URL(`https://${request.language}.wikipedia.org/w/api.php`)
  url.search = new URLSearchParams({ action: 'query', list: 'search', srsearch: request.query, srnamespace: '0', srlimit: '5',
    srprop: 'snippet|timestamp', format: 'json', formatversion: '2', utf8: '1' }).toString()
  const response = await transport.read(url.href, { accept: 'application/json', maxBytes: 2 * 1024 * 1024, maxRedirects: 0, signal })
  if (response.status !== 200 || response.url !== url.href) throw new TypeError('Wikipedia search was unavailable.')
  let data: unknown
  try { data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(response.body)) }
  catch { throw new TypeError('Wikipedia response is invalid.') }
  return parseWikipediaSearchResult(data, request.language, response.fetchedAt)
}
