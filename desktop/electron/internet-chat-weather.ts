/** Resolve an explicitly named place and return bounded weather evidence without guessing location or selecting ambiguous names. */

import type { ChatInternetSource } from './protocol.js'
import type { InternetReadResponse, InternetTransport } from './internet-transport.js'
import { InternetServiceError, readInternetWeather, readInternetWeatherForecast } from './internet-services.js'

interface Place {
  readonly latitude: number; readonly longitude: number; readonly name: string; readonly label: string
  readonly country: string | null; readonly countryCode: string
}
const MAX_BYTES = 128 * 1024

function invalid(): never { throw new InternetServiceError('invalid_output') }
function object(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return invalid()
  return value as Record<string, unknown>
}
function text(value: unknown, maximum = 256): string {
  if (typeof value !== 'string' || value.trim() === '' || Buffer.byteLength(value) > maximum
    || /[\p{Cc}\p{Cs}]/u.test(value)) return invalid()
  return value.trim()
}
function places(response: InternetReadResponse, url: string): Place[] {
  if (response.status !== 200) throw new InternetServiceError('unavailable')
  if (response.url !== url || !(response.body instanceof Uint8Array) || response.body.byteLength > MAX_BYTES
    || !/^application\/json(?:\s*;|$)/iu.test(response.headers['content-type'] ?? '')
    || !Number.isFinite(Date.parse(response.fetchedAt)) || new Date(response.fetchedAt).toISOString() !== response.fetchedAt) return invalid()
  let parsed: unknown
  try { parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(response.body)) }
  catch { return invalid() }
  const root = object(parsed)
  if (root.error !== undefined && root.error !== false) throw new InternetServiceError('unavailable')
  if (root.results === undefined) return []
  if (!Array.isArray(root.results) || root.results.length > 10) return invalid()
  const ids = new Set<number>()
  return root.results.map(value => {
    const place = object(value)
    if (!Number.isSafeInteger(place.id) || (place.id as number) < 1 || ids.has(place.id as number)
      || typeof place.latitude !== 'number' || !Number.isFinite(place.latitude) || Math.abs(place.latitude) > 90
      || typeof place.longitude !== 'number' || !Number.isFinite(place.longitude) || Math.abs(place.longitude) > 180) return invalid()
    ids.add(place.id as number)
    const name = text(place.name)
    const country = text(place.country_code, 2)
    if (!/^[A-Z]{2}$/u.test(country)) return invalid()
    const label = [...new Set([name, ...['admin2', 'admin1', 'country'].flatMap(key => place[key] === undefined || place[key] === '' ? [] : [text(place[key])]), country])].join(', ')
    return { name, label, latitude: place.latitude, longitude: place.longitude,
      country: place.country === undefined || place.country === '' ? null : text(place.country), countryCode: country }
  })
}
function normalized(value: string): string { return value.normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase('en').trim() }
function countryMatches(place: Place, country: string, language: 'en' | 'zh'): boolean {
  const names = [place.country]
  // Provider labels follow the answer language. The returned ISO country code
  // also supplies standard English/localized country names, so an explicitly
  // written English country remains usable in an otherwise Chinese question.
  for (const locale of new Set([language, 'en'])) {
    names.push(new Intl.DisplayNames([locale], { type: 'region' }).of(place.countryCode) ?? null)
  }
  return names.some(name => name !== null && normalized(name) === normalized(country))
}
function bounded(value: string): string {
  let output = '', bytes = 0
  for (const character of value) {
    const size = Buffer.byteLength(character)
    if (bytes + size > 4096) break
    output += character; bytes += size
  }
  return output
}

/** Resolve only the supplied city, ask for clarification on ambiguity, and fetch current or explicitly dated model weather. */
export async function readChatWeather(transport: Pick<InternetTransport, 'read'>,
  request: { query: string; language: 'en' | 'zh'; when?: 'now' | 'today' | 'tomorrow' }, signal: AbortSignal): Promise<ChatInternetSource[]> {
  if (typeof request.query !== 'string' || request.query.trim() === '' || Buffer.byteLength(request.query) > 512
    || /[\p{Cc}\p{Cs}]/u.test(request.query) || !['en', 'zh'].includes(request.language)
    || request.when !== undefined && !['now', 'today', 'tomorrow'].includes(request.when)) throw new InternetServiceError('invalid_request')
  const parts = request.query.split(/[,，]/u).map(part => part.trim())
  if (parts.length > 3 || parts.some(part => part === '')) throw new InternetServiceError('invalid_request')
  const country = parts[2], countryCode = country !== undefined && /^[A-Za-z]{2}$/u.test(country) ? country.toUpperCase() : undefined
  const url = new URL('https://geocoding-api.open-meteo.com/v1/search')
  // The provider supports one country/admin1 qualifier after a comma. A third
  // explicit ISO country code uses its own field so it cannot be ignored. A
  // full third country name is checked against returned country labels below;
  // the provider otherwise silently ignores everything after the second comma.
  url.search = new URLSearchParams({ name: parts.slice(0, 2).join(', '), count: '10', language: request.language, format: 'json',
    ...(countryCode === undefined ? {} : { countryCode }) }).toString()
  const deadline = new AbortController(), cancel = (): void => deadline.abort()
  signal.addEventListener('abort', cancel, { once: true })
  if (signal.aborted) cancel()
  const timer = setTimeout(cancel, 45000)
  const check = (): void => { if (deadline.signal.aborted) throw new InternetServiceError('cancelled') }
  try {
    check()
    const response = await transport.read(url.href, { accept: 'application/json', maxBytes: MAX_BYTES, maxRedirects: 0, signal: deadline.signal })
    check()
    const candidates = places(response, url.href).filter(candidate => country === undefined
      || (countryCode === undefined ? countryMatches(candidate, country, request.language)
        : candidate.countryCode === countryCode))
    // Prefix search can return suburbs alongside an exact city name. Exact
    // matches are relevant; two identical names still require clarification,
    // irrespective of provider ranking or population.
    const exact = candidates.filter(candidate => normalized(candidate.name) === normalized(parts[0]!))
    const matches = exact.length > 0 ? exact : candidates
    if (matches.length !== 1) {
      const zh = request.language === 'zh'
      const explanation = matches.length === 0
        ? zh ? '没有找到该地点，城市及省州或国家尚未明确；未取得天气数据。' : 'No matching place was found. The city and state or country are unresolved; no weather data was retrieved.'
        : zh ? '该地名对应多个地点，省州或国家尚未明确；未取得天气数据。候选地点：'
          : 'This name matches multiple places; the state or country is unresolved. No weather data was retrieved. Candidates:'
      const details = matches.slice(0, 5).map(candidate => candidate.label).join('; ')
      return [{ provider: 'weather', title: zh ? '天气查询需要明确地点' : 'Weather location needs clarification',
        url: url.href, fetchedAt: response.fetchedAt, snippet: bounded(`${explanation}${details ? ` ${details}` : ''}`) }]
    }
    const place = matches[0]!, location = { latitude: place.latitude, longitude: place.longitude }
    const source = request.when === 'today' || request.when === 'tomorrow'
      ? await readInternetWeatherForecast(transport, location, request.when, deadline.signal)
      : await readInternetWeather(transport, location, deadline.signal)
    check()
    return [{ provider: 'weather', title: `${place.label}: ${source.title}`, url: source.url,
      fetchedAt: source.fetchedAt, snippet: bounded(`Resolved place: ${place.label}.\n${source.content ?? source.snippet}`) }]
  } finally { clearTimeout(timer); signal.removeEventListener('abort', cancel) }
}
