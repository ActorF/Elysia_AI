/**
 * Run bounded local OCR through a trusted installed Tesseract executable.
 *
 * Canonical PNG bytes arrive from Main's owned image decoder, never a renderer
 * path. Fixed arguments, stdin/stdout, and a closed language set prevent OCR
 * text from selecting commands or files. Cancelling kills only the owned child
 * and retains physical ownership until its actual close event has drained.
 */

import { spawn, type ChildProcessWithoutNullStreams, type SpawnOptionsWithoutStdio } from 'node:child_process'
import { lstat, realpath } from 'node:fs/promises'
import path from 'node:path'
import { parseVisionOcrVersion } from './vision-analysis-protocol.js'

/** One available trained-data language; runtime paths never appear in readiness. */
export type OcrInstalledLanguage = 'eng' | 'chi_sim' | 'chi_tra'
/** Closed recognition modes require English alone or one Chinese language plus English. */
export type OcrLanguage = 'eng' | 'chi_sim+eng' | 'chi_tra+eng'
/** Fixed diagnostic categories exclude native stderr, command arguments, and image content. */
export type OcrErrorCode = 'unavailable' | 'busy' | 'cancelled' | 'timeout' | 'invalid_image'
  | 'invalid_language' | 'invalid_output' | 'limit_exceeded' | 'native_failed'

/** Describe the actual installed engine and language data without installing anything. */
export interface OcrReadiness {
  status: 'ready' | 'missing_engine' | 'missing_languages' | 'unavailable'
  languages: OcrInstalledLanguage[]
  version: string | null
}

/** One original-image pixel box and confidence normalized from Tesseract's 0..100 scale. */
export interface OcrWord { text: string; x: number; y: number; width: number; height: number; confidence: number }
/** Bounded plain OCR evidence; truncation is never silently presented as complete. */
export interface OcrResult {
  text: string
  words: OcrWord[]
  language: OcrLanguage
  width: number
  height: number
  truncated: false
}

/** Trusted Main-only native injection makes tests inert and keeps renderer data away from spawn. */
export interface OcrNativeAdapter {
  /** Start only the supplied reviewed local executable with the fixed adapter arguments. */
  spawn(executable: string, arguments_: readonly string[], options: SpawnOptionsWithoutStdio): ChildProcessWithoutNullStreams
}

/** Startup-only runtime selection is independent of renderer requests and OCR text. */
export interface OcrOptions {
  executable?: string | null
  tessdataDirectory?: string | null
  timeoutMs?: number
  native?: OcrNativeAdapter
}

const LANGUAGES: OcrInstalledLanguage[] = ['eng', 'chi_sim', 'chi_tra']
const MODES: OcrLanguage[] = ['eng', 'chi_sim+eng', 'chi_tra+eng']
const INPUT_BYTES = 16 * 1024 * 1024
const INPUT_PIXELS = 16 * 1024 * 1024
const MAX_EDGE = 8192
const OUTPUT_BYTES = 1024 * 1024
const MAX_TEXT_BYTES = 32 * 1024
const MAX_WORDS = 2048
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
const TSV_HEADER = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext'

/** Report only a closed safe failure; callers never receive native runtime paths or stderr. */
export class OcrError extends Error {
  /** Attach a fixed code that the controlled Main service can map into its public error contract. */
  constructor(readonly code: OcrErrorCode) { super(`Local OCR failed: ${code}.`) }
}

function input(png: Uint8Array, width: number, height: number): Buffer {
  if (!(png instanceof Uint8Array) || png.byteLength < 33 || png.byteLength > INPUT_BYTES
    || !Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1
    || width > MAX_EDGE || height > MAX_EDGE || width * height > INPUT_PIXELS) throw new OcrError('invalid_image')
  const bytes = Buffer.from(png)
  // Full decoding belongs to the canonical Main input owner. This independent
  // transport guard still binds dimensions and format to its immutable bytes.
  if (!bytes.subarray(0, 8).equals(PNG_SIGNATURE) || bytes.readUInt32BE(8) !== 13
    || bytes.toString('ascii', 12, 16) !== 'IHDR'
    || bytes.readUInt32BE(16) !== width || bytes.readUInt32BE(20) !== height) throw new OcrError('invalid_image')
  return bytes
}

function decode(bytes: Buffer): string {
  try { return new TextDecoder('utf-8', { fatal: true }).decode(bytes) }
  catch { throw new OcrError('invalid_output') }
}

function integer(value: string): number {
  if (!/^\d{1,9}$/u.test(value)) throw new OcrError('invalid_output')
  return Number(value)
}

function tsv(bytes: Buffer, width: number, height: number, language: OcrLanguage): OcrResult {
  const rows = decode(bytes).replaceAll('\r\n', '\n').split('\n')
  if (rows.shift() !== TSV_HEADER) throw new OcrError('invalid_output')
  const words: OcrWord[] = []
  const lines: string[] = []
  const seen = new Set<string>()
  let lineKey: string | null = null
  let text = ''
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index]!
    if (row === '' && index === rows.length - 1) continue
    const cells = row.split('\t')
    if (cells.length !== 12) throw new OcrError('invalid_output')
    const [level, page, block, paragraph, line, word, x, y, boxWidth, boxHeight] = cells.slice(0, 10).map(integer)
    if (level! < 1 || level! > 5 || page !== 1 || x! + boxWidth! > width || y! + boxHeight! > height) {
      throw new OcrError('invalid_output')
    }
    if (level !== 5) continue
    const confidenceText = cells[10]!
    const recognized = cells[11]!
    if (!/^\d{1,3}(?:\.\d{1,12})?$/u.test(confidenceText) || !Number.isFinite(Number(confidenceText))
      || Number(confidenceText) > 100 || block === 0 || paragraph === 0 || line === 0 || word === 0
      || boxWidth === 0 || boxHeight === 0 || recognized === ''
      || Array.from(recognized).some((character) => (character.codePointAt(0) ?? 0) < 32 || character === '\u007f')) {
      throw new OcrError('invalid_output')
    }
    const key = [page, block, paragraph, line, word].join(':')
    if (seen.has(key)) throw new OcrError('invalid_output')
    seen.add(key)
    if (words.length >= MAX_WORDS) throw new OcrError('limit_exceeded')
    words.push({ text: recognized, x: x!, y: y!, width: boxWidth!, height: boxHeight!, confidence: Number(confidenceText) / 100 })
    const currentLine = [page, block, paragraph, line].join(':')
    // TSV hierarchy preserves line breaks without inventing reading order or
    // reinterpreting punctuation as markup. Boxes retain exact source pixels.
    if (lineKey !== currentLine && text !== '') { lines.push(text); text = '' }
    lineKey = currentLine
    text += (text === '' ? '' : ' ') + recognized
    if (Buffer.byteLength([...lines, text].join('\n'), 'utf8') > MAX_TEXT_BYTES) throw new OcrError('limit_exceeded')
  }
  if (text !== '') lines.push(text)
  return { text: lines.join('\n'), words, language, width, height, truncated: false }
}

/** Own one OCR probe or recognition until all actual native I/O has closed. */
export class TesseractOcrAdapter {
  private busy = false
  private readonly timeoutMs: number
  private readonly native: OcrNativeAdapter

  /** Select a trusted startup runtime; construction never probes, installs, captures, or recognizes an image. */
  constructor(private readonly options: OcrOptions = {}) {
    this.timeoutMs = options.timeoutMs ?? 20_000
    if (!Number.isInteger(this.timeoutMs) || this.timeoutMs < 1 || this.timeoutMs > 20_000) throw new OcrError('unavailable')
    this.native = options.native ?? { spawn: (executable, args, options_) => spawn(executable, [...args], { ...options_, stdio: 'pipe' }) }
  }

  /** Retain physical ownership even after cancellation or timeout requests until the native child closes. */
  isBusy(): boolean { return this.busy }

  /** Enumerate only installed English/Chinese data; absent runtime or languages never trigger a download. */
  async readiness(signal: AbortSignal = new AbortController().signal): Promise<OcrReadiness> {
    return this.owned(signal, async (deadline) => {
      try {
        const executable = await this.executable()
        if (executable === null) return { status: 'missing_engine', languages: [], version: null }
        return await this.probe(executable, deadline, signal)
      } catch (error: unknown) {
        if (error instanceof OcrError && ['cancelled', 'timeout', 'busy'].includes(error.code)) throw error
        return { status: 'unavailable', languages: [], version: null }
      }
    })
  }

  /** Recognize one owned canonical PNG with exact bounded word positions, never writing source images or output files. */
  async run(png: Uint8Array, width: number, height: number, language: OcrLanguage, signal: AbortSignal): Promise<OcrResult> {
    const bytes = input(png, width, height)
    if (!MODES.includes(language)) throw new OcrError('invalid_language')
    return this.owned(signal, async (deadline) => {
      const executable = await this.executable()
      if (executable === null) throw new OcrError('unavailable')
      const available = await this.probe(executable, deadline, signal)
      if (language.split('+').some((required) => !available.languages.includes(required as OcrInstalledLanguage))) {
        throw new OcrError('unavailable')
      }
      const directory = await this.dataDirectory()
      const args = ['stdin', 'stdout', '--oem', '1', '--psm', '11', '-l', language,
        ...(directory === null ? [] : ['--tessdata-dir', directory]), 'tsv']
      return tsv(await this.invoke(executable, args, bytes, OUTPUT_BYTES, deadline, signal), width, height, language)
    })
  }

  private async owned<Result>(signal: AbortSignal, operation: (deadline: number) => Promise<Result>): Promise<Result> {
    if (this.busy) throw new OcrError('busy')
    if (!(signal instanceof AbortSignal)) throw new OcrError('unavailable')
    if (signal.aborted) throw new OcrError('cancelled')
    this.busy = true
    try {
      const result = await operation(performance.now() + this.timeoutMs)
      if (signal.aborted) throw new OcrError('cancelled')
      return result
    } catch (error: unknown) { throw error instanceof OcrError ? error : new OcrError('native_failed') }
    finally { this.busy = false }
  }

  private async executable(): Promise<string | null> {
    const selected = this.options.executable === undefined
      ? process.env.ELYSIA_TESSERACT_PATH
      : this.options.executable
    const candidates = selected !== undefined ? [selected] : process.platform === 'win32'
      ? [path.join(process.env.ProgramFiles ?? 'C:\\Program Files', 'Tesseract-OCR', 'tesseract.exe'),
        ...(process.env.LOCALAPPDATA === undefined ? []
          : [path.join(process.env.LOCALAPPDATA, 'Programs', 'Tesseract-OCR', 'tesseract.exe')])]
      : ['/usr/bin/tesseract']
    // A user installation needs no administrator rights. Only absence of the
    // fixed machine runtime permits fallback; explicit or unsafe choices never
    // silently authorize another executable, PATH search, or registry discovery.
    for (const candidate of candidates) {
      if (candidate === null) return null
      try { return await this.localPath(candidate, false) }
      catch (error: unknown) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
    }
    return null
  }

  private async dataDirectory(): Promise<string | null> {
    const selected = this.options.tessdataDirectory
    return selected === undefined || selected === null ? null : this.localPath(selected, true)
  }

  private async localPath(value: string, directory: boolean): Promise<string> {
    if (typeof value !== 'string' || !path.isAbsolute(value) || value.length > 32767 || value.startsWith('\\\\')
      || Array.from(value).some((character) => (character.codePointAt(0) ?? 0) < 32) || /[<>"|?*]/u.test(value)
      || (process.platform === 'win32' && !/^[A-Za-z]:\\/u.test(value))) throw new OcrError('unavailable')
    const components = value.slice(path.parse(value).root.length).split(path.sep)
    if (components.some((component) => component === '' || component === '.' || component === '..'
      || component !== component.trim() || component.endsWith('.') || component.includes(':')
      || /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/iu.test(component))) throw new OcrError('unavailable')
    // Check the selected spelling from the root before resolving it. A dangling
    // link or redirected ancestor must fail closed, not resemble an absent
    // machine installation and unlock fallback to a different runtime.
    let cursor = path.parse(value).root
    for (const component of components) {
      cursor = path.join(cursor, component)
      if ((await lstat(cursor)).isSymbolicLink()) throw new OcrError('unavailable')
    }
    const resolved = await realpath(value)
    if (resolved.startsWith('\\\\')) throw new OcrError('unavailable')
    const details = await lstat(resolved)
    if (details.isSymbolicLink() || (directory ? !details.isDirectory() : !details.isFile() || details.nlink !== 1)) {
      throw new OcrError('unavailable')
    }
    // Resolve full spelling, then reject redirected ancestors of the selected
    // spelling too. This runtime selection is startup authority, not a tool.
    for (let cursor = path.resolve(value); ; cursor = path.dirname(cursor)) {
      if ((await lstat(cursor)).isSymbolicLink()) throw new OcrError('unavailable')
      if (path.dirname(cursor) === cursor) break
    }
    return resolved
  }

  private async probe(executable: string, deadline: number, signal: AbortSignal): Promise<OcrReadiness> {
    const versionText = decode(await this.invoke(executable, ['--version'], null, 32 * 1024, deadline, signal))
    const reported = /^tesseract[ \t]+(\S+)(?:\s|$)/u.exec(versionText)?.[1]
    let version: string
    try { version = parseVisionOcrVersion(reported) }
    catch { throw new OcrError('unavailable') }
    const directory = await this.dataDirectory()
    const output = decode(await this.invoke(executable, [...(directory === null ? [] : ['--tessdata-dir', directory]), '--list-langs'], null,
      32 * 1024, deadline, signal))
    const listed = new Set(output.replaceAll('\r\n', '\n').split('\n').map((line) => line.trim()))
    const languages = LANGUAGES.filter((language) => listed.has(language))
    return { status: languages.includes('eng') ? 'ready' : 'missing_languages', languages, version }
  }

  private invoke(executable: string, args: readonly string[], bytes: Buffer | null, maximum: number,
    deadline: number, signal: AbortSignal): Promise<Buffer> {
    if (signal.aborted) return Promise.reject(new OcrError('cancelled'))
    const remaining = deadline - performance.now()
    if (remaining <= 0) return Promise.reject(new OcrError('timeout'))
    return new Promise((resolve, reject) => {
      let failure: OcrError | null = null
      let closed = false
      let outputBytes = 0
      let stderrBytes = 0
      const output: Buffer[] = []
      const env: NodeJS.ProcessEnv = { ...process.env, OMP_THREAD_LIMIT: '1' }
      delete env.TESSDATA_PREFIX
      const child = this.native.spawn(executable, args, { shell: false, windowsHide: true, detached: false,
        cwd: path.dirname(executable), env })
      const stop = (code: OcrErrorCode): void => {
        if (closed || failure !== null) return
        failure = new OcrError(code)
        // The promise deliberately stays pending if termination is delayed.
        // Only close proves that stdout/stderr and this owned child drained.
        if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
      }
      const cancel = (): void => stop('cancelled')
      const timer = setTimeout(() => stop('timeout'), remaining)
      signal.addEventListener('abort', cancel, { once: true })
      child.stdout.on('data', (chunk: Buffer) => {
        if (failure !== null) return
        outputBytes += chunk.byteLength
        if (outputBytes > maximum) { stop('limit_exceeded'); return }
        output.push(Buffer.from(chunk))
      })
      child.stderr.on('data', (chunk: Buffer) => {
        stderrBytes += chunk.byteLength
        if (stderrBytes > 16 * 1024) stop('limit_exceeded')
      })
      child.once('error', () => stop('native_failed'))
      child.stdin.on('error', () => stop('native_failed'))
      child.once('close', (code: number | null) => {
        closed = true
        clearTimeout(timer)
        signal.removeEventListener('abort', cancel)
        if (failure !== null) reject(failure)
        else if (signal.aborted) reject(new OcrError('cancelled'))
        else if (code !== 0) reject(new OcrError('native_failed'))
        else resolve(Buffer.concat(output, outputBytes))
      })
      if (signal.aborted) cancel()
      child.stdin.end(bytes ?? undefined)
    })
  }
}
