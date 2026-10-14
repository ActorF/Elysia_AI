/** Extract bounded inert UTF-8 page text and claimed dates without executing scripts or loading remote subresources. */

import { randomUUID } from 'node:crypto'
import { parse, type DefaultTreeAdapterMap } from 'parse5'
import type { InternetSource } from './internet-protocol.js'
import type { InternetTransport } from './internet-transport.js'

/** A page result uses the shared source fields and identifies its distinct reader provenance. */
export type InternetPageSource = Omit<InternetSource, 'provider'> & { readonly provider: 'web' }
/** Reader failures are fixed categories, never page content, native diagnostics or credentials. */
export type InternetReaderErrorCode = 'unsupported_type' | 'invalid_encoding' | 'invalid_document'
  | 'empty_document' | 'limit_exceeded' | 'cancelled'
/** Carry a stable extraction failure across Main's existing safe error boundary. */
export class InternetReaderError extends Error {
  /** Attach only the closed public category when decoding or extraction fails. */
  constructor(readonly code: InternetReaderErrorCode) { super(`Web reader: ${code}.`) }
}

type TreeNode = DefaultTreeAdapterMap['node']
type Element = DefaultTreeAdapterMap['element']
const MAX_BODY_BYTES = 1024 * 1024
const MAX_CONTENT_BYTES = 32 * 1024
const MAX_METADATA_BYTES = 2048
const MAX_NODES = 100_000
const EXCLUDED = new Set(['head', 'nav', 'header', 'footer', 'form', 'script', 'style', 'template', 'noscript',
  'button', 'input', 'textarea', 'select', 'option', 'iframe', 'object', 'embed', 'svg', 'math'])
const BLOCKS = new Set(['p', 'div', 'section', 'article', 'main', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'hr',
  'ul', 'ol', 'li', 'table', 'tr', 'td', 'th', 'blockquote', 'pre', 'dl', 'dt', 'dd', 'address'])
const PUBLICATION_KEYS = new Set(['article:published_time', 'og:published_time', 'datepublished', 'date-published', 'pubdate'])
const MODIFICATION_KEYS = new Set(['article:modified_time', 'og:updated_time', 'datemodified', 'date-modified', 'last-modified'])

function fail(code: InternetReaderErrorCode): never { throw new InternetReaderError(code) }
function checkCancelled(signal: AbortSignal): void { if (signal.aborted) fail('cancelled') }
function element(node: TreeNode): node is Element { return 'tagName' in node }
function attribute(node: Element, name: string): string | undefined {
  return node.attrs.find(item => item.name === name)?.value
}
function children(node: TreeNode): readonly TreeNode[] { return 'childNodes' in node ? node.childNodes : [] }
function clean(value: string): string {
  return value.replace(/\r\n?/gu, '\n').replace(/[\u2028\u2029]/gu, '\n')
    .replace(/\p{Cc}/gu, character => '\t\n\r'.includes(character) ? character : ' ')
    .split('\n').map(line => line.replace(/[\t \u00a0]+/gu, ' ').trim()).join('\n').replace(/\n{3,}/gu, '\n\n').trim()
}
function bounded(value: string, maxBytes: number): { value: string; truncated: boolean } {
  if (Buffer.byteLength(value, 'utf8') <= maxBytes) return { value, truncated: false }
  let bytes = 0, result = ''
  // UTF-8 limits apply to complete Unicode code points; cutting a Buffer could
  // manufacture replacement characters in Chinese text or supplementary glyphs.
  for (const character of value) {
    const length = Buffer.byteLength(character, 'utf8')
    if (bytes + length > maxBytes) break
    result += character; bytes += length
  }
  return { value: result.trimEnd(), truncated: true }
}
function hidden(node: Element): boolean {
  const style = attribute(node, 'style') ?? ''
  return attribute(node, 'hidden') !== undefined || attribute(node, 'inert') !== undefined
    || attribute(node, 'aria-hidden')?.trim().toLowerCase() === 'true'
    || /(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important\s*)?(?:;|$)/iu.test(style)
}

function contentType(value: unknown): 'html' | 'plain' {
  if (typeof value !== 'string' || value.length > 512) fail('unsupported_type')
  const [mime, ...parameters] = value.split(';')
  const type = mime?.trim().toLowerCase()
  if (type !== 'text/html' && type !== 'text/plain') fail('unsupported_type')
  for (const parameter of parameters) {
    if (parameter.trim() === '') continue
    const match = /^\s*charset\s*=\s*(?:"([^"\r\n]+)"|([^\s;"\r\n]+))\s*$/iu.exec(parameter)
    // Accept only the one implemented charset; do not reinterpret legacy bytes
    // as UTF-8 or silently ignore contradictory/unknown MIME parameters.
    if (match === null || !/^(?:utf-8|utf8)$/iu.test(match[1] ?? match[2] ?? '')) fail('invalid_encoding')
  }
  return type === 'text/html' ? 'html' : 'plain'
}

function isoDate(value: string | undefined): string | null {
  if (value === undefined || value.length > 40) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,3})?(?:Z|([+-])(\d{2}):(\d{2}))$/u.exec(value)
  if (match === null) return null
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3])
  if (month < 1 || month > 12 || day < 1 || day > 31 || Number(match[4]) > 23 || Number(match[5]) > 59
    || Number(match[6]) > 59 || Number(match[8] ?? 0) > 23 || Number(match[9] ?? 0) > 59) return null
  const calendar = new Date(0)
  calendar.setUTCFullYear(year, month - 1, day)
  if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day) return null
  const milliseconds = Date.parse(value)
  return Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : null
}
function lastModified(value: string | undefined): string | null {
  if (value === undefined || !/^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$/u.test(value)) return null
  const milliseconds = Date.parse(value)
  if (!Number.isFinite(milliseconds) || new Date(milliseconds).toUTCString() !== value) return null
  return new Date(milliseconds).toISOString()
}
function oneDate(values: readonly string[]): string | null {
  const dates = new Set(values.map(isoDate).filter(value => value !== null))
  // Conflicting document claims are unknown, not whichever metadata tag wins
  // insertion order. Page dates remain claims rather than verified event times.
  return dates.size === 1 ? dates.values().next().value! : null
}

function extractText(root: TreeNode, includeHead = false): string {
  const stack: Array<TreeNode | '\n'> = [root], pieces: string[] = []
  let visited = 0
  while (stack.length > 0) {
    const node = stack.pop()!
    if (node === '\n') { pieces.push('\n'); continue }
    if (++visited > MAX_NODES) fail('limit_exceeded')
    if (element(node)) {
      if ((!includeHead && EXCLUDED.has(node.tagName)) || hidden(node)) continue
      if (BLOCKS.has(node.tagName)) { pieces.push('\n'); stack.push('\n') }
    }
    if ('value' in node && node.nodeName === '#text') pieces.push(node.value)
    const nested = children(node)
    for (let index = nested.length - 1; index >= 0; index -= 1) stack.push(nested[index]!)
  }
  return clean(pieces.join(''))
}

function extractHtml(html: string): { title: string; text: string; publishedAt: string | null; modifiedAt: string | null } {
  let document: DefaultTreeAdapterMap['document']
  try { document = parse(html, { scriptingEnabled: true }) }
  catch { return fail('invalid_document') }
  const publications: string[] = [], modifications: string[] = []
  const stack: Array<{ node: TreeNode; inHead: boolean }> = [{ node: document, inHead: false }]
  let title = '', body: Element | null = null, article: Element | null = null, main: Element | null = null, visited = 0
  while (stack.length > 0) {
    const { node, inHead: parentInHead } = stack.pop()!
    if (++visited > MAX_NODES) fail('limit_exceeded')
    const inHead = parentInHead || element(node) && node.tagName === 'head'
    if (element(node)) {
      if (node.tagName !== 'head' && (EXCLUDED.has(node.tagName) || hidden(node))) continue
      if (inHead && node.tagName === 'title' && title === '') title = extractText(node, true)
      if (inHead && node.tagName === 'meta') {
        const charset = attribute(node, 'charset')
        if (charset !== undefined && !/^(?:utf-8|utf8)$/iu.test(charset.trim())) fail('invalid_encoding')
        const content = attribute(node, 'content')
        if (attribute(node, 'http-equiv')?.toLowerCase() === 'content-type' && content !== undefined) contentType(content)
        const key = (attribute(node, 'property') ?? attribute(node, 'name') ?? '').toLowerCase()
        if (content !== undefined && PUBLICATION_KEYS.has(key)) publications.push(content)
        if (content !== undefined && MODIFICATION_KEYS.has(key)) modifications.push(content)
      }
      if (!inHead) {
        if (node.tagName === 'body') body = node
        if (node.tagName === 'article' && article === null) article = node
        if (node.tagName === 'main' && main === null) main = node
      }
    }
    const nested = children(node)
    for (let index = nested.length - 1; index >= 0; index -= 1) stack.push({ node: nested[index]!, inHead })
  }
  // Prefer the author's semantic content boundary, with a body fallback for
  // ordinary sites. This is static text extraction, not CSS/browser rendering.
  const text = (article === null ? '' : extractText(article)) || (main === null ? '' : extractText(main))
    || (body === null ? '' : extractText(body))
  return { title, text, publishedAt: oneDate(publications), modifiedAt: oneDate(modifications) }
}

/** Read one explicitly selected public page through the shared policy and return only detached plain text.
 *
 * The transport owns domain, DNS, redirect and physical cancellation checks.
 * parse5 builds an inert tree: links, images, frames, CSS and scripts never
 * execute or trigger follow-up requests. Only valid UTF-8 HTML/plain text is
 * supported; document dates and Last-Modified remain unverified source claims.
 */
export async function readInternetPage(
  transport: Pick<InternetTransport, 'read'>,
  url: string,
  signal: AbortSignal,
): Promise<InternetPageSource> {
  checkCancelled(signal)
  const response = await transport.read(url, { accept: 'text/html, text/plain', maxBytes: MAX_BODY_BYTES, maxRedirects: 3, signal })
  checkCancelled(signal)
  if (response.status !== 200) fail('invalid_document')
  if (!(response.body instanceof Uint8Array) || response.body.byteLength > MAX_BODY_BYTES) fail('limit_exceeded')
  const type = contentType(response.headers['content-type'])
  let decoded: string
  try { decoded = new TextDecoder('utf-8', { fatal: true }).decode(response.body) }
  catch { return fail('invalid_encoding') }
  let finalUrl: URL
  try { finalUrl = new URL(response.url) }
  catch { return fail('invalid_document') }
  if (response.url.length > 4096 || finalUrl.protocol !== 'https:' || finalUrl.username !== '' || finalUrl.password !== ''
    || finalUrl.hash !== '' || finalUrl.port !== '' && finalUrl.port !== '443') fail('invalid_document')
  const fetchedAt = isoDate(response.fetchedAt)
  if (fetchedAt === null) fail('invalid_document')
  const extracted = type === 'html' ? extractHtml(decoded)
    : { title: '', text: clean(decoded), publishedAt: null, modifiedAt: null }
  if (extracted.text === '') fail('empty_document')
  const content = bounded(extracted.text, MAX_CONTENT_BYTES)
  checkCancelled(signal)
  return { id: `internet_${randomUUID().replaceAll('-', '')}`, provider: 'web', url: finalUrl.href,
    title: bounded(extracted.title || finalUrl.hostname, MAX_METADATA_BYTES).value,
    snippet: bounded(content.value.replace(/\s+/gu, ' '), MAX_METADATA_BYTES).value,
    content: content.value, publishedAt: extracted.publishedAt,
    modifiedAt: extracted.modifiedAt ?? lastModified(response.headers['last-modified']), fetchedAt,
    fromCache: false, truncated: content.truncated }
}
