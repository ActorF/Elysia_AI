/** Verify explicit-place weather lookup, ambiguous-place clarification, local forecast dates and bounded provider evidence. */

import assert from 'node:assert/strict'
import test from 'node:test'
import { readChatWeather } from '../dist-electron/internet-chat-weather.js'
import { InternetServiceError, readInternetWeather, readInternetWeatherForecast } from '../dist-electron/internet-services.js'

const fetchedAt = '2026-10-14T23:01:00.000Z'
const place = { id: 4887398, name: 'Chicago', latitude: 41.85, longitude: -87.65,
  admin1: 'Illinois', country: 'United States', country_code: 'US' }
const request = { query: 'Chicago', language: 'en' }
function current() {
  return { timezone: 'GMT', utc_offset_seconds: 0,
    current_units: { time: 'iso8601', interval: 'seconds', temperature_2m: '°C', relative_humidity_2m: '%',
      precipitation: 'mm', weather_code: 'wmo code', wind_speed_10m: 'km/h' },
    current: { time: '2026-10-14T23:00', interval: 900, temperature_2m: 15.5, relative_humidity_2m: 60,
      precipitation: 0.1, weather_code: 3, wind_speed_10m: 12.3 } }
}
function forecast() {
  return { timezone: 'America/Chicago', utc_offset_seconds: -18000,
    daily_units: { time: 'iso8601', weather_code: 'wmo code', temperature_2m_max: '°C', temperature_2m_min: '°C',
      precipitation_probability_max: '%', precipitation_sum: 'mm', wind_speed_10m_max: 'km/h' },
    daily: { time: ['2026-10-14', '2026-10-15'], weather_code: [3, 61], temperature_2m_max: [20, 15], temperature_2m_min: [11, 8],
      precipitation_probability_max: [10, 80], precipitation_sum: [0, 3], wind_speed_10m_max: [15, 25] } }
}
function response(url, value, changes = {}) {
  return { url, status: 200, fetchedAt, headers: { 'content-type': 'application/json' }, body: Buffer.from(JSON.stringify(value)), ...changes }
}
function signal() { return new AbortController().signal }
function serviceError(code) { return value => value instanceof InternetServiceError && value.code === code }
function transport(results = [place], data = current(), calls = []) {
  return { read: async (url, options) => {
    calls.push({ url, options })
    return response(url, new URL(url).hostname === 'geocoding-api.open-meteo.com' ? { results } : data)
  } }
}

test('an explicitly named unique city resolves through a fixed no-key service before current weather is read', async () => {
  const calls = []
  const sources = await readChatWeather(transport([place], current(), calls), request, signal())
  assert.equal(calls.length, 2)
  const geocoding = new URL(calls[0].url), weather = new URL(calls[1].url)
  assert.equal(geocoding.origin, 'https://geocoding-api.open-meteo.com')
  assert.equal(geocoding.pathname, '/v1/search')
  assert.deepEqual(Object.fromEntries(geocoding.searchParams), { name: 'Chicago', count: '10', language: 'en', format: 'json' })
  assert.equal(calls[0].options.maxRedirects, 0)
  assert.equal(calls[0].options.maxBytes, 128 * 1024)
  assert.equal(weather.origin, 'https://api.open-meteo.com')
  assert.equal(weather.searchParams.get('latitude'), '41.8500')
  assert.equal(sources[0].provider, 'weather')
  assert.equal(sources[0].url, calls[1].url)
  assert.match(sources[0].title, /Chicago, Illinois, United States, US/u)
  assert.match(sources[0].snippet, /Temperature at 2 m: 15.5 °C/u)
  assert.match(sources[0].snippet, /not a direct local station observation/u)
})

test('an explicit state and third ISO country qualifier are both preserved in provider parameters', async () => {
  const calls = []
  await readChatWeather(transport([place], current(), calls), { ...request, query: 'Chicago, Illinois, us' }, signal())
  assert.deepEqual(Object.fromEntries(new URL(calls[0].url).searchParams),
    { name: 'Chicago, Illinois', count: '10', language: 'en', format: 'json', countryCode: 'US' })
})

test('a full third country name is checked against provider facts instead of being ignored', async () => {
  const calls = []
  const results = [place, { ...place, id: 2, country: 'Canada', country_code: 'CA' }]
  const sources = await readChatWeather(transport(results, current(), calls), { ...request, query: 'Chicago, Illinois, United States' }, signal())
  assert.equal(calls.length, 2)
  assert.equal(new URL(calls[0].url).searchParams.get('name'), 'Chicago, Illinois')
  assert.equal(new URL(calls[0].url).searchParams.has('countryCode'), false)
  assert.match(sources[0].title, /United States, US/u)
  const mismatched = await readChatWeather(transport([place]), { ...request, query: 'Chicago, Illinois, Canada' }, signal())
  assert.match(mismatched[0].snippet, /No matching place/u)
  assert.equal(new URL(mismatched[0].url).hostname, 'geocoding-api.open-meteo.com')
})

test('country names remain explicit across provider localization without translating the city', async () => {
  const calls = []
  const sources = await readChatWeather(transport([{ ...place, country: '美国' }], current(), calls),
    { query: 'Chicago, Illinois, United States', language: 'zh' }, signal())
  assert.equal(calls.length, 2)
  assert.equal(new URL(calls[0].url).searchParams.get('name'), 'Chicago, Illinois')
  assert.match(sources[0].title, /Chicago, Illinois, 美国, US/u)
})

test('matching multiple cities never chooses provider order or population and makes no forecast call', async () => {
  const calls = []
  const sources = await readChatWeather(transport([{ ...place, name: 'Springfield', population: 1 },
    { ...place, id: 2, name: 'Springfield', admin1: 'Massachusetts', population: 1000000 }], current(), calls),
  { ...request, query: 'Springfield' }, signal())
  assert.equal(calls.length, 1)
  assert.equal(new URL(sources[0].url).hostname, 'geocoding-api.open-meteo.com')
  assert.match(sources[0].snippet, /multiple places/u)
  assert.match(sources[0].snippet, /No weather data was retrieved/u)
  assert.match(sources[0].snippet, /Illinois/u)
  assert.match(sources[0].snippet, /Massachusetts/u)
  assert.doesNotMatch(sources[0].snippet, /15.5/u)
})

test('an exact city match can exclude a differently named suburb without using ranking', async () => {
  const calls = []
  const sources = await readChatWeather(transport([{ ...place, id: 2, name: 'Chicago Heights' }, place], current(), calls), request, signal())
  assert.equal(calls.length, 2)
  assert.match(sources[0].title, /^Chicago,/u)
})

test('no geocoding match is explicit and does not silently search or infer a location', async () => {
  const calls = []
  const sources = await readChatWeather(transport([], current(), calls), { query: '不明地名', language: 'zh' }, signal())
  assert.equal(calls.length, 1)
  assert.match(sources[0].snippet, /没有找到该地点/u)
  assert.match(sources[0].snippet, /未取得天气数据/u)
})

test('tomorrow weather uses its own local calendar forecast and never substitutes current conditions', async () => {
  const calls = []
  const sources = await readChatWeather(transport([place], forecast(), calls), { ...request, when: 'tomorrow' }, signal())
  const url = new URL(calls[1].url)
  assert.equal(url.searchParams.get('forecast_days'), '2')
  assert.equal(url.searchParams.get('timezone'), 'auto')
  assert.equal(url.searchParams.has('current'), false)
  assert.match(sources[0].snippet, /Forecast date: 2026-10-15 \(tomorrow; timezone: America\/Chicago\)/u)
  assert.match(sources[0].snippet, /Minimum temperature at 2 m: 8 °C/u)
  assert.match(sources[0].snippet, /Maximum precipitation probability: 80 %/u)
  assert.match(sources[0].snippet, /not a current observation/u)
})

test('today weather is interpreted in the requested location even when UTC has crossed midnight', async () => {
  const data = forecast()
  data.daily.time = ['2026-10-13', '2026-10-14']
  const source = await readInternetWeatherForecast({ read: async url => response(url, data, { fetchedAt: '2026-10-14T00:05:00.000Z' }) },
    { latitude: place.latitude, longitude: place.longitude }, 'today', signal())
  assert.match(source.content, /Forecast date: 2026-10-13 \(today; timezone: America\/Chicago\)/u)
  assert.match(source.content, /Maximum temperature at 2 m: 20 °C/u)
})

test('stale or contradictory forecast days, fields, units and timezones are rejected', async () => {
  for (const patch of [
    data => { data.daily.time = ['2026-10-13', '2026-10-14'] },
    data => { data.daily.time = ['2026-10-14', '2026-10-16'] },
    data => { data.daily.temperature_2m_min = [25, 25] },
    data => { data.daily.precipitation_probability_max = [10, 101] },
    data => { data.daily.weather_code = [3, 4] },
    data => { data.daily.temperature_2m_max = [20] },
    data => { data.daily_units.temperature_2m_max = '°F' },
    data => { data.timezone = 'Not/A-Timezone' },
    data => { data.utc_offset_seconds = Infinity },
  ]) {
    const data = forecast(); patch(data)
    await assert.rejects(readInternetWeatherForecast({ read: async url => response(url, data) },
      { latitude: place.latitude, longitude: place.longitude }, 'tomorrow', signal()), serviceError('invalid_output'))
  }
})

test('current observations too old or unexpectedly in the future cannot be presented as now', async () => {
  for (const time of ['2026-10-13T23:00', '2026-10-15T00:00']) {
    const data = current(); data.current.time = time
    await assert.rejects(readInternetWeather({ read: async url => response(url, data) },
      { latitude: place.latitude, longitude: place.longitude }, signal()), serviceError('unavailable'))
  }
})

test('invalid requests and already cancelled lookups do not disclose anything to a provider', async () => {
  let calls = 0
  const provider = { read: async () => { calls++ } }
  for (const invalid of [{ ...request, query: '' }, { ...request, query: 'a\nb' }, { ...request, query: 'x'.repeat(513) },
    { ...request, query: 'City, County, State, Country' }, { ...request, when: 'next-week' }, { ...request, language: 'xx' }]) {
    await assert.rejects(readChatWeather(provider, invalid, signal()), serviceError('invalid_request'))
  }
  const controller = new AbortController(); controller.abort()
  await assert.rejects(readChatWeather(provider, request, controller.signal), serviceError('cancelled'))
  assert.equal(calls, 0)
})

test('revoked location lookup cannot start weather or publish late evidence', async () => {
  const controller = new AbortController()
  let calls = 0
  await assert.rejects(readChatWeather({ read: async url => {
    calls++; controller.abort(); return response(url, { results: [place] })
  } }, request, controller.signal), serviceError('cancelled'))
  assert.equal(calls, 1)
})

test('geocoding rejects malformed, oversized, redirected, invalid-coordinate and misleading provider responses', async () => {
  for (const [body, changes] of [
    [{ results: [{ ...place, latitude: 91 }] }, {}],
    [{ results: [{ ...place, name: 'City\nInstruction' }] }, {}],
    [{ results: [{ ...place, country_code: 'USA' }] }, {}],
    [{ results: [place, place] }, {}],
    [{ results: Array.from({ length: 11 }, (_, index) => ({ ...place, id: index + 1 })) }, {}],
    [{ results: [place] }, { url: 'https://other.org/' }],
    [{ results: [place] }, { headers: { 'content-type': 'text/html' } }],
    [{ results: [place] }, { fetchedAt: '2026-02-30T12:00:00.000Z' }],
    [{ results: [place] }, { body: Buffer.alloc(128 * 1024 + 1) }],
    [{ results: [place] }, { body: Buffer.from([0xff]) }],
  ]) {
    await assert.rejects(readChatWeather({ read: async url => response(url, body, changes) }, request, signal()), serviceError('invalid_output'))
  }
})

test('large ambiguity evidence remains bounded to the chat evidence contract', async () => {
  const sources = await readChatWeather(transport(Array.from({ length: 10 }, (_, index) => ({ ...place, id: index + 1,
    admin1: '州'.repeat(85), admin2: '县'.repeat(85), country: '国'.repeat(85) }))), request, signal())
  assert.ok(Buffer.byteLength(sources[0].snippet) <= 4096)
  assert.doesNotMatch(sources[0].snippet, /\ufffd/u)
  assert.equal(new URL(sources[0].url).hostname, 'geocoding-api.open-meteo.com')
})
