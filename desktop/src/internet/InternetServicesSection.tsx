/** Collect explicit public-service inputs without detecting location, fetching on edits, or creating a separate request lifecycle. */

import { useState } from 'react'
import type { InternetPolicyState } from '../../electron/internet-policy-protocol.ts'
import { parseInternetGithubRequest, parseInternetWeatherRequest, type InternetGithubRequest, type InternetWeatherRequest } from '../../electron/internet-protocol.ts'
import './InternetServicesSection.css'

interface InternetServicesSectionProps {
  chatId: string
  policy: InternetPolicyState | null
  disabled: boolean
  /** Start a validated weather request through the parent research lease. */
  onWeather(request: InternetWeatherRequest): void
  /** Start a validated public repository request through the parent research lease. */
  onGithub(request: InternetGithubRequest): void
}

/** Present manual coordinates and public repository names; the parent owns all asynchronous admission and source publication. */
export function InternetServicesSection({ chatId, policy, disabled, onWeather, onGithub }: InternetServicesSectionProps) {
  const [latitude, setLatitude] = useState('')
  const [longitude, setLongitude] = useState('')
  const [owner, setOwner] = useState('')
  const [repo, setRepo] = useState('')
  let weather: InternetWeatherRequest | null = null
  let github: InternetGithubRequest | null = null
  try {
    // Empty strings must not become zero coordinates. Decimal text also avoids
    // accepting JavaScript numeric syntax the user would not expect as a location.
    if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/u.test(latitude.trim()) && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/u.test(longitude.trim())) {
      weather = parseInternetWeatherRequest({ chatId, latitude: Number(latitude), longitude: Number(longitude) })
    }
  } catch { /* Invalid coordinates remain a local draft. */ }
  try { github = parseInternetGithubRequest({ chatId, owner: owner.trim(), repo: repo.trim() }) }
  catch { /* Incomplete names and URL/path syntax never become requests. */ }
  const weatherAllowed = policy?.enabled === true && policy.allowedDomains.includes('api.open-meteo.com')
  const githubAllowed = policy?.enabled === true && policy.allowedDomains.includes('api.github.com')

  return <section className="internet-services" aria-label="Public Internet services">
    <section aria-label="Weather lookup">
      <h3>Weather by coordinates</h3>
      <p>Enter latitude and longitude. Open-Meteo receives these coordinates rounded to four decimal places only when you click Get weather. No location is detected automatically.</p>
      <div className="internet-service-fields">
        <label>Latitude<input inputMode="decimal" value={latitude} maxLength={24} disabled={disabled} placeholder="−90 to 90" onChange={event => { setLatitude(event.target.value) }} /></label>
        <label>Longitude<input inputMode="decimal" value={longitude} maxLength={24} disabled={disabled} placeholder="−180 to 180" onChange={event => { setLongitude(event.target.value) }} /></label>
      </div>
      {weather !== null && <p>Coordinates to share: {weather.latitude.toFixed(4)}, {weather.longitude.toFixed(4)}.</p>}
      {weather === null && (latitude !== '' || longitude !== '') && <p>Enter decimal latitude from −90 to 90 and longitude from −180 to 180.</p>}
      {!weatherAllowed && <p>Enable Internet and allow api.open-meteo.com in Internet settings.</p>}
      <p>Current conditions come from weather models, rather than a direct local station observation. Provider time, retrieval time and measurement units accompany the result.</p>
      <button type="button" disabled={disabled || weather === null || !weatherAllowed} onClick={() => { if (weather !== null && weatherAllowed) onWeather(weather) }}>Get weather</button>
    </section>
    <section aria-label="Public GitHub repository lookup">
      <h3>Public GitHub repository</h3>
      <p>Read public repository metadata without a token. Enter the owner and repository names separately; only a click contacts api.github.com.</p>
      <div className="internet-service-fields">
        <label>GitHub owner<input value={owner} maxLength={39} disabled={disabled} onChange={event => { setOwner(event.target.value) }} /></label>
        <label>Repository name<input value={repo} maxLength={100} disabled={disabled} onChange={event => { setRepo(event.target.value) }} /></label>
      </div>
      {github === null && (owner !== '' || repo !== '') && <p>Enter valid owner and repository names, without a URL or slash.</p>}
      {!githubAllowed && <p>Enable Internet and allow api.github.com in Internet settings.</p>}
      <button type="button" disabled={disabled || github === null || !githubAllowed} onClick={() => { if (github !== null && githubAllowed) onGithub(github) }}>Get repository</button>
    </section>
  </section>
}
