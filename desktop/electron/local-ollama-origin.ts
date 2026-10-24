/** Share strict Ollama loopback validation and typed local-model setup recovery across desktop boundaries. */

export const LOCAL_OLLAMA_ORIGIN_ERROR = 'Ollama must use local HTTP: choose localhost, 127.0.0.1, or [::1] with the local service port. Remote Ollama, credentials, and URL paths are not supported.'
export const LOCAL_OLLAMA_MODEL_ERROR = 'Choose an installed local Ollama model; cloud model names are not supported.'

/** Validate an explicit loopback origin and return a literal host that never requires DNS. */
export function localOllamaTarget(value: unknown): { hostname: string; port: number } {
  // Inspect the raw authority first: WHATWG URL otherwise silently turns short,
  // hexadecimal, or octal IPv4 spellings into a different, apparently safe URL.
  const match = typeof value === 'string' && value.length <= 2048 && !/\s/u.test(value)
    ? /^http:\/\/(localhost|127(?:\.(?:0|[1-9][0-9]{0,2})){3}|\[[0-9a-f:]+\])(?::([0-9]{1,5}))?\/?$/iu.exec(value)
    : null
  if (match === null) throw new TypeError(LOCAL_OLLAMA_ORIGIN_ERROR)
  let parsed: URL
  try { parsed = new URL(value as string) }
  catch { throw new TypeError(LOCAL_OLLAMA_ORIGIN_ERROR) }
  const hostname = parsed.hostname === 'localhost' ? '127.0.0.1'
    : parsed.hostname === '[::1]' ? '::1' : parsed.hostname
  const port = match[2] === undefined ? 80 : Number(match[2])
  // All of 127/8 is loopback, matching Python's ipaddress contract. Expanded
  // IPv6 is accepted only when URL canonicalization proves it is exactly ::1.
  if ((!/^127\.(?:\d{1,3}\.){2}\d{1,3}$/u.test(hostname) && hostname !== '::1')
    || !Number.isSafeInteger(port) || port < 1 || port > 65535) {
    throw new TypeError(LOCAL_OLLAMA_ORIGIN_ERROR)
  }
  return { hostname, port }
}

/** Reject only explicit cloud suffixes, retaining arbitrary installed local model aliases. */
export function isCloudOllamaModel(value: string): boolean {
  return /(?::|-)(?:cloud)$/iu.test(value.trim())
}

/** Identify authenticated local-model startup failures independently of displayed or translated diagnostics. */
export function isLocalModelSetupError(code: string | undefined): boolean {
  return code === 'backend.ollama_unavailable'
    || code === 'backend.model_missing'
    || code === 'backend.model_failed'
}
