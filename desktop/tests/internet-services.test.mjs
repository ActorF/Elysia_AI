/** Verify fixed public API requests, honest source times/units and bounded credential-free evidence. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { InternetServiceError, parseInternetGithubRepository, parseInternetWeatherLocation,
  readInternetGithubRepository, readInternetWeather } from '../dist-electron/internet-services.js'

const location = { latitude: 41.8781, longitude: -87.6298 }
const repository = { owner: 'octocat', repo: 'Hello-World' }
const fetchedAt = '2026-10-14T15:01:00.000Z'
function weather() {
  return { timezone: 'GMT', utc_offset_seconds: 0,
    current_units: { time: 'iso8601', interval: 'seconds', temperature_2m: '°C', relative_humidity_2m: '%',
      precipitation: 'mm', weather_code: 'wmo code', wind_speed_10m: 'km/h' },
    current: { time: '2026-10-14T15:00', interval: 900, temperature_2m: 15.5, relative_humidity_2m: 60,
      precipitation: 0.1, weather_code: 3, wind_speed_10m: 12.3 } }
}
function github() {
  return { name: 'Hello-World', full_name: 'octocat/Hello-World', private: false, visibility: 'public',
    html_url: 'https://github.com/octocat/Hello-World', url: 'https://api.github.com/repos/octocat/Hello-World',
    description: 'Example repository.', default_branch: 'main', archived: false, stargazers_count: 42,
    open_issues_count: 3, updated_at: '2026-10-13T11:00:00Z', pushed_at: '2026-10-12T10:00:00Z' }
}
function transport(value, changes = {}, calls = []) {
  return { read: async (url, options) => {
    calls.push({ url, options })
    return { url, status: 200, headers: { 'content-type': 'application/json' }, body: Buffer.from(JSON.stringify(value)), fetchedAt, ...changes }
  } }
}
function signal() { return new AbortController().signal }
function error(code) { return value => value instanceof InternetServiceError && value.code === code }

test('Weather uses explicit rounded coordinates, fixed fields and units without credentials or redirects', async () => {
  const calls = [], abort = signal()
  const source = await readInternetWeather(transport(weather(), {}, calls),
    { latitude: 41.8781234, longitude: -87.6298432 }, abort)
  assert.equal(calls.length, 1)
  const url = new URL(calls[0].url)
  assert.equal(url.origin, 'https://api.open-meteo.com')
  assert.equal(url.pathname, '/v1/forecast')
  assert.deepEqual(Object.fromEntries(url.searchParams), { latitude: '41.8781', longitude: '-87.6298',
    current: 'temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m',
    timezone: 'UTC', temperature_unit: 'celsius', wind_speed_unit: 'kmh', precipitation_unit: 'mm' })
  assert.deepEqual(calls[0].options, { accept: 'application/json', maxBytes: 128 * 1024, maxRedirects: 0, signal: abort })
  assert.equal(source.url, calls[0].url)
  assert.equal(source.provider, 'weather')
  assert.equal(source.publishedAt, null)
  assert.equal(source.modifiedAt, null)
  assert.equal(source.fetchedAt, fetchedAt)
  assert.equal(source.fromCache, false)
  assert.equal(source.truncated, false)
  assert.match(source.content, /Provider time \(UTC\): 2026-10-14T15:00:00.000Z/u)
  assert.match(source.content, /Provider interval: 900 seconds/u)
  assert.match(source.content, /Temperature at 2 m: 15.5 °C/u)
  assert.match(source.content, /Relative humidity at 2 m: 60 %/u)
  assert.match(source.content, /Precipitation: 0.1 mm/u)
  assert.match(source.content, /Wind speed at 10 m: 12.3 km\/h/u)
  assert.match(source.content, /Overcast \(WMO 3\)/u)
  assert.match(source.content, /not a direct local station observation/u)
  assert.match(source.id, /^internet_[0-9a-f]{32}$/u)
})

test('Coordinate validation prevents inference, coercion and extra data from reaching a provider', async () => {
  assert.deepEqual(parseInternetWeatherLocation({ latitude: -0.00001, longitude: -0 }), { latitude: 0, longitude: 0 })
  assert.deepEqual(parseInternetWeatherLocation({ latitude: 90, longitude: 180 }), { latitude: 90, longitude: 180 })
  const calls = []
  for (const value of [null, {}, { latitude: '41', longitude: 5 }, { latitude: NaN, longitude: 5 },
    { latitude: 90.00001, longitude: 5 }, { latitude: -90.00001, longitude: 5 }, { latitude: 41, longitude: Infinity },
    { latitude: 41, longitude: -180.00001 }, { ...location, autoLocate: true }, { ...location, secret: 'private' }]) {
    await assert.rejects(readInternetWeather(transport(weather(), {}, calls), value, signal()), error('invalid_request'))
  }
  assert.equal(calls.length, 0)
})

test('Weather accepts clearly labeled partial missing values but never invents all-missing conditions', async () => {
  const data = weather()
  data.current.temperature_2m = null
  data.current.weather_code = null
  const source = await readInternetWeather(transport(data), location, signal())
  assert.match(source.content, /Temperature at 2 m: Unavailable/u)
  assert.match(source.content, /Conditions: Unavailable/u)
  for (const key of ['relative_humidity_2m', 'precipitation', 'wind_speed_10m']) data.current[key] = null
  await assert.rejects(readInternetWeather(transport(data), location, signal()), error('unavailable'))
})

test('Weather rejects contradictory units, invalid numbers, unsupported codes and ambiguous source times', async () => {
  const patches = [
    data => { data.utc_offset_seconds = -18000 },
    data => { data.timezone = 'America/Chicago' },
    data => { data.current_units.temperature_2m = '°F' },
    data => { data.current_units.wind_speed_10m = 'mph' },
    data => { data.current_units.precipitation = 'inch' },
    data => { data.current.relative_humidity_2m = 101 },
    data => { data.current.wind_speed_10m = -1 },
    data => { data.current.temperature_2m = '15.5' },
    data => { data.current.precipitation = -0.1 },
    data => { data.current.weather_code = 4 },
    data => { data.current.weather_code = 3.5 },
    data => { data.current.interval = 0 },
    data => { data.current.time = '2026-02-30T12:00' },
    data => { data.current.time = '2026-10-14T15:00:00-05:00' },
    data => { data.error = true },
  ]
  for (const patch of patches) {
    const data = weather(); patch(data)
    await assert.rejects(readInternetWeather(transport(data), location, signal()), error('invalid_output'))
  }
})

test('GitHub performs one fixed public read and preserves honest counts, source URL and repository dates', async () => {
  const calls = [], abort = signal()
  const source = await readInternetGithubRepository(transport(github(), {}, calls), repository, abort)
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'https://api.github.com/repos/octocat/Hello-World')
  assert.deepEqual(calls[0].options, { accept: 'application/json', maxBytes: 128 * 1024, maxRedirects: 0, signal: abort })
  assert.equal(source.provider, 'github')
  assert.equal(source.url, calls[0].url)
  assert.equal(source.publishedAt, null)
  assert.equal(source.modifiedAt, '2026-10-13T11:00:00.000Z')
  assert.equal(source.fetchedAt, fetchedAt)
  assert.match(source.content, /Default branch: main/u)
  assert.match(source.content, /Stars: 42/u)
  assert.match(source.content, /Open issues and pull requests: 3/u)
  assert.match(source.content, /Repository page: https:\/\/github.com\/octocat\/Hello-World/u)
  assert.match(source.content, /Last push \(UTC\): 2026-10-12T10:00:00.000Z/u)
  assert.match(source.content, /not a count of issues alone/u)
})

test('GitHub accepts case-insensitive lookup identity and optional empty repository facts', async () => {
  const data = github()
  data.description = null; data.pushed_at = null
  const source = await readInternetGithubRepository(transport(data), { owner: 'OCTOCAT', repo: 'hello-world' }, signal())
  assert.match(source.content, /No description provided/u)
  assert.match(source.content, /Last push \(UTC\): Unknown/u)
  assert.equal(source.title, 'GitHub: octocat/Hello-World')
  assert.deepEqual(parseInternetGithubRepository({ owner: 'example-org', repo: '.github' }), { owner: 'example-org', repo: '.github' })
})

test('Repository components cannot smuggle paths, URLs, credentials or an arbitrary service into the request', async () => {
  const calls = []
  for (const value of [null, {}, { owner: '../other', repo: 'repo' }, { owner: 'name', repo: '../repo' },
    { owner: 'name', repo: '..' }, { owner: '-name', repo: 'repo' }, { owner: 'n'.repeat(40), repo: 'repo' },
    { owner: 'name', repo: 'r'.repeat(101) }, { owner: 'name', repo: 'repo?token=secret' },
    { owner: 'name@host', repo: 'repo' }, { owner: 'name', repo: '%2fother' }, { ...repository, token: 'private' }]) {
    await assert.rejects(readInternetGithubRepository(transport(github(), {}, calls), value, signal()), error('invalid_request'))
  }
  assert.equal(calls.length, 0)
})

test('GitHub rejects private, substituted, contradictory or malformed repository evidence', async () => {
  const patches = [
    data => { data.private = true },
    data => { data.visibility = 'private' },
    data => { data.full_name = 'other/Hello-World' },
    data => { data.name = 'other' },
    data => { data.html_url = 'https://other.net/octocat/Hello-World' },
    data => { data.url = 'https://api.github.com/repos/other/Hello-World' },
    data => { data.stargazers_count = -1 },
    data => { data.open_issues_count = 3.5 },
    data => { data.archived = 'no' },
    data => { data.updated_at = '2026-02-30T00:00:00Z' },
    data => { data.pushed_at = 'yesterday' },
    data => { data.description = 'secret\u0000control' },
    data => { data.description = '\ud800' },
  ]
  for (const patch of patches) {
    const data = github(); patch(data)
    await assert.rejects(readInternetGithubRepository(transport(data), repository, signal()), error('invalid_output'))
  }
})

test('Long Unicode descriptions are visibly truncated without splitting code points or source budgets', async () => {
  const data = github()
  data.description = '中文😀'.repeat(700)
  const source = await readInternetGithubRepository(transport(data), repository, signal())
  assert.equal(source.truncated, true)
  assert.ok(Buffer.byteLength(source.snippet, 'utf8') <= 2048)
  assert.ok(Buffer.byteLength(source.content, 'utf8') <= 32768)
  assert.ok(!source.content.includes('\ufffd'))
  const second = await readInternetGithubRepository(transport(github()), repository, signal())
  assert.notEqual(second.id, source.id)
})

test('Both services reject redirects, error statuses, invalid JSON/UTF8 and oversized responses', async () => {
  for (const [operation, request, data] of [[readInternetWeather, location, weather()], [readInternetGithubRepository, repository, github()]]) {
    for (const status of [301, 403, 404, 429, 500]) {
      await assert.rejects(operation(transport(data, { status }), request, signal()), error('unavailable'))
    }
    await assert.rejects(operation(transport(data, { url: 'https://other.net/' }), request, signal()), error('invalid_output'))
    for (const body of [Buffer.from('{broken'), Buffer.from([0xc3, 0x28]), Buffer.from('[]')]) {
      await assert.rejects(operation(transport(data, { body }), request, signal()), error('invalid_output'))
    }
    await assert.rejects(operation(transport(data, { headers: { 'content-type': 'text/html' } }), request, signal()), error('invalid_output'))
    await assert.rejects(operation(transport(data, { body: Buffer.alloc(128 * 1024 + 1) }), request, signal()), error('limit_exceeded'))
    await assert.rejects(operation(transport(data, { fetchedAt: '2026-02-30T00:00:00Z' }), request, signal()), error('invalid_output'))
  }
})

test('Cancellation is checked before admission and after actual transport settlement for both providers', async () => {
  for (const [operation, request, data] of [[readInternetWeather, location, weather()], [readInternetGithubRepository, repository, github()]]) {
    const cancelled = new AbortController(), calls = []
    cancelled.abort()
    await assert.rejects(operation(transport(data, {}, calls), request, cancelled.signal), error('cancelled'))
    assert.equal(calls.length, 0)
    const abort = new AbortController()
    let finish
    const pending = operation({ read: async (url) => new Promise(resolve => {
      finish = () => resolve({ url, status: 200, headers: { 'content-type': 'application/json' },
        body: Buffer.from(JSON.stringify(data)), fetchedAt })
    }) }, request, abort.signal)
    abort.abort(); finish()
    await assert.rejects(pending, error('cancelled'))
  }
})

test('Both services retain shared policy failures and never retry a failed provider read', async () => {
  const failure = new Error('controlled policy refusal')
  for (const [operation, request] of [[readInternetWeather, location], [readInternetGithubRepository, repository]]) {
    let calls = 0
    await assert.rejects(operation({ read: async () => { calls += 1; throw failure } }, request, signal()), value => value === failure)
    assert.equal(calls, 1)
  }
})
