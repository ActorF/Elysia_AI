/** Read explicit public weather and GitHub facts through the shared Internet policy without credentials or inferred location. */

import { randomUUID } from 'node:crypto'
import type { InternetSource } from './internet-protocol.js'
import type { InternetReadResponse, InternetTransport } from './internet-transport.js'

/** Coordinates are explicitly entered and rounded to four decimal places before disclosure to the provider. */
export interface InternetWeatherLocation { readonly latitude: number; readonly longitude: number }
/** Select one public repository by bounded path components, never by an arbitrary URL or credential. */
export interface InternetGithubRepository { readonly owner: string; readonly repo: string }
/** Public service evidence follows the common source contract with its specific provider provenance. */
export type InternetServiceSource = Omit<InternetSource, 'provider'> & { readonly provider: 'weather' | 'github' }
/** Provider failures expose stable categories without response bodies, tokens or internal request details. */
export type InternetServiceErrorCode = 'invalid_request' | 'invalid_output' | 'unavailable' | 'limit_exceeded' | 'cancelled'
/** Separate explicit input errors from unavailable or malformed public service responses. */
export class InternetServiceError extends Error {
  /** Preserve a fixed category for the controller's renderer-safe error mapping. */
  constructor(readonly code: InternetServiceErrorCode) { super(`Internet service: ${code}.`) }
}

const MAX_RESPONSE_BYTES = 128 * 1024
const CONDITIONS: Readonly<Record<number, string>> = {
  0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast', 45: 'Fog', 48: 'Depositing rime fog',
  51: 'Light drizzle', 53: 'Moderate drizzle', 55: 'Dense drizzle', 56: 'Light freezing drizzle', 57: 'Dense freezing drizzle',
  61: 'Slight rain', 63: 'Moderate rain', 65: 'Heavy rain', 66: 'Light freezing rain', 67: 'Heavy freezing rain',
  71: 'Slight snow', 73: 'Moderate snow', 75: 'Heavy snow', 77: 'Snow grains',
  80: 'Slight rain showers', 81: 'Moderate rain showers', 82: 'Violent rain showers',
  85: 'Slight snow showers', 86: 'Heavy snow showers', 95: 'Thunderstorm',
  96: 'Thunderstorm with slight hail', 99: 'Thunderstorm with heavy hail',
}
function fail(code: InternetServiceErrorCode): never { throw new InternetServiceError(code) }
function checkCancelled(signal: AbortSignal): void { if (signal.aborted) fail('cancelled') }
function object(value: unknown, code: InternetServiceErrorCode = 'invalid_output'): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) fail(code)
  return value as Record<string, unknown>
}
function requestObject(value: unknown, keys: readonly string[]): Record<string, unknown> {
  const checked = object(value, 'invalid_request')
  if (Reflect.ownKeys(checked).length !== keys.length || keys.some(key => !Object.hasOwn(checked, key))) fail('invalid_request')
  return checked
}
function text(value: unknown, maxBytes: number, empty = false): string {
  if (typeof value !== 'string' || !empty && value.trim() === '' || Buffer.byteLength(value, 'utf8') > maxBytes
    || [...value].some(character => {
      const point = character.codePointAt(0)!
      return point < 32 && !'\t\n\r'.includes(character) || point === 127 || point >= 0xd800 && point <= 0xdfff
    })) fail('invalid_output')
  return value
}
function bounded(value: string, maxBytes = 2048): { text: string; truncated: boolean } {
  let result = '', bytes = 0
  for (const character of value) {
    const size = Buffer.byteLength(character, 'utf8')
    if (bytes + size > maxBytes) return { text: result, truncated: true }
    result += character; bytes += size
  }
  return { text: result, truncated: false }
}
function timestamp(value: unknown, naiveUtc = false): string {
  let candidate = text(value, 32)
  // Open-Meteo omits the zone on current.time. Interpret that form only after
  // the caller verifies the response's explicit zero UTC offset and timezone.
  if (naiveUtc && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/u.test(candidate)) {
    candidate = `${candidate.length === 16 ? `${candidate}:00` : candidate}Z`
  }
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u.test(candidate)) fail('invalid_output')
  const parsed = new Date(candidate)
  const canonical = candidate.includes('.') ? candidate : candidate.replace('Z', '.000Z')
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString() !== canonical) fail('invalid_output')
  return canonical
}
function number(value: unknown, minimum: number, maximum: number, nullable = false): number | null {
  if (nullable && value === null) return null
  if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) fail('invalid_output')
  return value
}
function integer(value: unknown, maximum = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0 || (value as number) > maximum) fail('invalid_output')
  return value as number
}
function jsonResponse(response: InternetReadResponse, requestedUrl: string): Record<string, unknown> {
  if (response.status !== 200) fail('unavailable')
  if (response.url !== requestedUrl) fail('invalid_output')
  if (!(response.body instanceof Uint8Array) || response.body.byteLength > MAX_RESPONSE_BYTES) fail('limit_exceeded')
  if (!/^application\/json(?:\s*;|$)/iu.test(response.headers['content-type'] ?? '')) fail('invalid_output')
  let decoded: unknown
  try { decoded = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(response.body)) }
  catch { return fail('invalid_output') }
  return object(decoded)
}
function source(provider: 'weather' | 'github', response: InternetReadResponse,
  title: string, snippet: string, content: string, modifiedAt: string | null, truncated = false): InternetServiceSource {
  const excerpt = bounded(snippet)
  return { id: `internet_${randomUUID().replaceAll('-', '')}`, provider, title, url: response.url,
    snippet: excerpt.text, content: text(content, 32768), publishedAt: null, modifiedAt,
    fetchedAt: timestamp(response.fetchedAt), fromCache: false, truncated: truncated || excerpt.truncated }
}

/** Accept only finite explicit geographic coordinates, canonicalizing negative zero and rounding for disclosure/cache identity. */
export function parseInternetWeatherLocation(value: unknown): InternetWeatherLocation {
  const request = requestObject(value, ['latitude', 'longitude'])
  if (typeof request.latitude !== 'number' || !Number.isFinite(request.latitude) || request.latitude < -90 || request.latitude > 90
    || typeof request.longitude !== 'number' || !Number.isFinite(request.longitude) || request.longitude < -180 || request.longitude > 180) fail('invalid_request')
  return { latitude: Number(request.latitude.toFixed(4)) || 0, longitude: Number(request.longitude.toFixed(4)) || 0 }
}

/** Accept exact GitHub owner/repository components without traversal, URL syntax, credentials or automatic name rewriting. */
export function parseInternetGithubRepository(value: unknown): InternetGithubRepository {
  const request = requestObject(value, ['owner', 'repo'])
  if (typeof request.owner !== 'string' || !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/u.test(request.owner)
    || typeof request.repo !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/u.test(request.repo)
    || request.repo === '.' || request.repo === '..') fail('invalid_request')
  return { owner: request.owner, repo: request.repo }
}

/** Read current model-derived weather for explicit coordinates; no location detection, secret, retry or background refresh is performed. */
export async function readInternetWeather(transport: Pick<InternetTransport, 'read'>, value: InternetWeatherLocation,
  signal: AbortSignal): Promise<InternetServiceSource> {
  const location = parseInternetWeatherLocation(value)
  checkCancelled(signal)
  const url = new URL('https://api.open-meteo.com/v1/forecast')
  url.search = new URLSearchParams({ latitude: location.latitude.toFixed(4), longitude: location.longitude.toFixed(4),
    current: 'temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m',
    timezone: 'UTC', temperature_unit: 'celsius', wind_speed_unit: 'kmh', precipitation_unit: 'mm' }).toString()
  const response = await transport.read(url.href, { accept: 'application/json', maxBytes: MAX_RESPONSE_BYTES, maxRedirects: 0, signal })
  checkCancelled(signal)
  const root = jsonResponse(response, url.href), current = object(root.current), units = object(root.current_units)
  if (root.error !== undefined && root.error !== false || root.utc_offset_seconds !== 0
    || !['UTC', 'GMT', 'Etc/UTC', 'Etc/GMT'].includes(root.timezone as string)
    || units.time !== 'iso8601' || units.interval !== 'seconds' || units.temperature_2m !== '°C'
    || units.relative_humidity_2m !== '%' || units.precipitation !== 'mm' || units.weather_code !== 'wmo code'
    || units.wind_speed_10m !== 'km/h') fail('invalid_output')
  const providerTime = timestamp(current.time, true), interval = integer(current.interval, 86400)
  if (interval === 0) fail('invalid_output')
  // Current conditions use quarter-hour model values. A generous three-hour
  // age window tolerates provider/cache delays without presenting an old day
  // as current; one future interval tolerates normal boundary rounding.
  const age = Date.parse(timestamp(response.fetchedAt)) - Date.parse(providerTime)
  if (age > 3 * 60 * 60 * 1000 || age < -15 * 60 * 1000) fail('unavailable')
  const temperature = number(current.temperature_2m, -150, 100, true)
  const humidity = number(current.relative_humidity_2m, 0, 100, true)
  const precipitation = number(current.precipitation, 0, 5000, true)
  const wind = number(current.wind_speed_10m, 0, 2000, true)
  const code = current.weather_code === null ? null : integer(current.weather_code, 99)
  if (code !== null && !Object.hasOwn(CONDITIONS, code)) fail('invalid_output')
  if ([temperature, humidity, precipitation, wind, code].every(item => item === null)) fail('unavailable')
  const display = (value: number | null, unit: string): string => value === null ? 'Unavailable' : `${value} ${unit}`
  const condition = code === null ? 'Unavailable' : `${CONDITIONS[code]} (WMO ${code})`
  const content = [`Open-Meteo model-derived current conditions near ${location.latitude.toFixed(4)}, ${location.longitude.toFixed(4)}.`,
    `Provider time (UTC): ${providerTime}`, `Provider interval: ${interval} seconds`,
    `Conditions: ${condition}`, `Temperature at 2 m: ${display(temperature, '°C')}`,
    `Relative humidity at 2 m: ${display(humidity, '%')}`, `Precipitation: ${display(precipitation, 'mm')}`,
    `Wind speed at 10 m: ${display(wind, 'km/h')}`, `Retrieved at (UTC): ${timestamp(response.fetchedAt)}`,
    'These conditions are derived from weather models; they are not a direct local station observation.'].join('\n')
  checkCancelled(signal)
  return source('weather', response, `Weather near ${location.latitude.toFixed(4)}, ${location.longitude.toFixed(4)}`,
    `${providerTime}: ${condition}; temperature ${display(temperature, '°C')}; wind ${display(wind, 'km/h')}. Model-derived conditions.`, content, null)
}

/** Read today's or tomorrow's model forecast in the selected place's timezone, never relabeling current conditions as a forecast. */
export async function readInternetWeatherForecast(transport: Pick<InternetTransport, 'read'>, value: InternetWeatherLocation,
  day: 'today' | 'tomorrow', signal: AbortSignal): Promise<InternetServiceSource> {
  const location = parseInternetWeatherLocation(value)
  if (day !== 'today' && day !== 'tomorrow') fail('invalid_request')
  checkCancelled(signal)
  const url = new URL('https://api.open-meteo.com/v1/forecast')
  url.search = new URLSearchParams({ latitude: location.latitude.toFixed(4), longitude: location.longitude.toFixed(4),
    daily: 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max',
    forecast_days: '2', timezone: 'auto', temperature_unit: 'celsius', wind_speed_unit: 'kmh', precipitation_unit: 'mm' }).toString()
  const response = await transport.read(url.href, { accept: 'application/json', maxBytes: MAX_RESPONSE_BYTES, maxRedirects: 0, signal })
  checkCancelled(signal)
  const root = jsonResponse(response, url.href), daily = object(root.daily), units = object(root.daily_units)
  const zone = text(root.timezone, 128), retrieved = timestamp(response.fetchedAt)
  if (root.error !== undefined && root.error !== false || !Number.isInteger(root.utc_offset_seconds)
    || Math.abs(root.utc_offset_seconds as number) > 86400 || units.time !== 'iso8601' || units.weather_code !== 'wmo code'
    || units.temperature_2m_max !== '°C' || units.temperature_2m_min !== '°C'
    || units.precipitation_probability_max !== '%' || units.precipitation_sum !== 'mm' || units.wind_speed_10m_max !== 'km/h') fail('invalid_output')
  let today: string
  try {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' })
      .formatToParts(new Date(retrieved))
    today = ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)?.value).join('-')
  } catch { return fail('invalid_output') }
  // Daily dates belong to the forecast location, not the computer's timezone.
  // Checking both days prevents a stale provider payload becoming "tomorrow".
  const tomorrow = new Date(`${today}T00:00:00Z`)
  tomorrow.setUTCDate(tomorrow.getUTCDate() + 1)
  const dates = [today, tomorrow.toISOString().slice(0, 10)]
  if (!Array.isArray(daily.time) || daily.time.length !== 2 || daily.time.some((date, index) => date !== dates[index])) fail('invalid_output')
  const dayIndex = day === 'today' ? 0 : 1
  const field = (key: string, minimum: number, maximum: number): number | null => {
    const values = daily[key]
    if (!Array.isArray(values) || values.length !== 2) return fail('invalid_output')
    return values.map(value => number(value, minimum, maximum, true))[dayIndex]!
  }
  const maximum = field('temperature_2m_max', -150, 100), minimum = field('temperature_2m_min', -150, 100)
  const probability = field('precipitation_probability_max', 0, 100), precipitation = field('precipitation_sum', 0, 5000)
  const wind = field('wind_speed_10m_max', 0, 2000), code = field('weather_code', 0, 99)
  if (maximum !== null && minimum !== null && maximum < minimum || code !== null && !Object.hasOwn(CONDITIONS, code)) fail('invalid_output')
  if ([maximum, minimum, probability, precipitation, wind, code].every(item => item === null)) fail('unavailable')
  const display = (value: number | null, unit: string): string => value === null ? 'Unavailable' : `${value} ${unit}`
  const condition = code === null ? 'Unavailable' : `${CONDITIONS[code]} (WMO ${code})`
  const content = [`Open-Meteo model forecast near ${location.latitude.toFixed(4)}, ${location.longitude.toFixed(4)}.`,
    `Forecast date: ${dates[dayIndex]} (${day}; timezone: ${zone})`, `Conditions: ${condition}`,
    `Minimum temperature at 2 m: ${display(minimum, '°C')}`, `Maximum temperature at 2 m: ${display(maximum, '°C')}`,
    `Maximum precipitation probability: ${display(probability, '%')}`, `Total precipitation: ${display(precipitation, 'mm')}`,
    `Maximum wind speed at 10 m: ${display(wind, 'km/h')}`, `Retrieved at (UTC): ${retrieved}`,
    'This is a model forecast for the whole local calendar day; it is not a current observation.'].join('\n')
  checkCancelled(signal)
  return source('weather', response, `Weather forecast for ${dates[dayIndex]}`,
    `${dates[dayIndex]} (${zone}): ${condition}; ${display(minimum, '°C')} to ${display(maximum, '°C')}. Model forecast.`, content, null)
}

/** Read bounded public repository metadata from GitHub without attaching credentials or following renamed-repository redirects. */
export async function readInternetGithubRepository(transport: Pick<InternetTransport, 'read'>, value: InternetGithubRepository,
  signal: AbortSignal): Promise<InternetServiceSource> {
  const request = parseInternetGithubRepository(value)
  checkCancelled(signal)
  const url = `https://api.github.com/repos/${encodeURIComponent(request.owner)}/${encodeURIComponent(request.repo)}`
  const response = await transport.read(url, { accept: 'application/json', maxBytes: MAX_RESPONSE_BYTES, maxRedirects: 0, signal })
  checkCancelled(signal)
  const root = jsonResponse(response, url), name = text(root.name, 100), fullName = text(root.full_name, 140)
  if (root.private !== false || root.visibility !== undefined && root.visibility !== 'public'
    || name.toLowerCase() !== request.repo.toLowerCase() || fullName.toLowerCase() !== `${request.owner}/${request.repo}`.toLowerCase()
    || typeof root.archived !== 'boolean') fail('invalid_output')
  const canonical = parseInternetGithubRepository({ owner: fullName.split('/')[0], repo: name })
  const repositoryUrl = `https://github.com/${canonical.owner}/${canonical.repo}`
  if (root.html_url !== repositoryUrl || root.url !== `https://api.github.com/repos/${canonical.owner}/${canonical.repo}`) fail('invalid_output')
  const description = bounded(root.description === null ? 'No description provided.' : text(root.description, 8192, true))
  const stars = integer(root.stargazers_count), issues = integer(root.open_issues_count)
  const branch = text(root.default_branch, 256), updatedAt = timestamp(root.updated_at)
  const pushedAt = root.pushed_at === null ? null : timestamp(root.pushed_at)
  const content = [`Public GitHub repository: ${fullName}`, `Description: ${description.text}`, `Default branch: ${branch}`,
    `Stars: ${stars}`, `Open issues and pull requests: ${issues}`, `Archived: ${root.archived ? 'Yes' : 'No'}`,
    `Repository updated at (UTC): ${updatedAt}`, `Last push (UTC): ${pushedAt ?? 'Unknown'}`,
    `Repository page: ${repositoryUrl}`, `Retrieved at (UTC): ${timestamp(response.fetchedAt)}`,
    'GitHub open_issues_count includes pull requests; it is not a count of issues alone.'].join('\n')
  checkCancelled(signal)
  return source('github', response, `GitHub: ${fullName}`,
    `${fullName}: ${stars} stars; ${issues} open issues and pull requests. ${description.text}`, content, updatedAt, description.truncated)
}
