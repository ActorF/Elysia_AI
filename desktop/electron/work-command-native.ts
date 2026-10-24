/** @fileoverview Execute an approved CMD through one trusted Python helper with bounded receipts and physical cancellation. */

import { spawn, type ChildProcess, type SpawnOptions } from 'node:child_process'
import path from 'node:path'
import { parseWorkToolResult, type WorkToolCall, type WorkToolResult } from './work-agent-protocol.js'

/** Trusted Main context; CMD receives the user's Windows authority, not a filesystem/network sandbox. */
export interface WorkCommandNativeHost {
  /** Recheck the frozen turn and its permission before dispatch or publication. */
  isCurrent(request: WorkToolCall): boolean
  projectRoot: string
  pythonExecutable: string
  defaultCwd: string
}

/** Replace only process creation and deadlines in lifecycle tests. */
export interface WorkCommandNativeDependencies {
  platform: NodeJS.Platform
  environment: NodeJS.ProcessEnv
  /** Launch the fixed helper, never a model-selected executable or shell. */
  spawnProcess(executable: string, args: readonly string[], options: SpawnOptions): ChildProcess
  cancellationGraceMs: number
}

type CommandArguments = { command: string; cwd: string; timeoutMs: number }
const OUTPUT_LIMIT = 262_144

function validText(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.trim().length > 0 && !value.includes('\0')
    && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)
    && Buffer.byteLength(value, 'utf8') <= maximum
}

function absolute(value: string): boolean {
  return /^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))/u.test(value)
}

function commandArguments(request: WorkToolCall, defaultCwd: string): CommandArguments {
  const value = request.arguments
  if (request.tool !== 'run_command' || value === null || Array.isArray(value) || typeof value !== 'object'
    || Object.keys(value).some(key => !['command', 'cwd', 'timeoutMs'].includes(key)) || !Object.hasOwn(value, 'command')) throw new TypeError('Invalid command request.')
  const cwd = Object.hasOwn(value, 'cwd') ? value.cwd : defaultCwd
  const timeoutMs = Object.hasOwn(value, 'timeoutMs') ? value.timeoutMs : 30_000
  // CMD /c would report success after only the first line of a multiline
  // proposal, so require explicit shell sequencing before it reaches Python.
  // UNC cwd also silently falls back to Windows instead of honoring the
  // reviewed folder. Only drive-qualified cwd is meaningful for this runner.
  if (!validText(value.command, 8192) || /[\r\n]/u.test(value.command) || !validText(cwd, 2048) || !/^[A-Za-z]:[\\/]/u.test(cwd)
    || typeof timeoutMs !== 'number' || !Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 120_000) throw new TypeError('Invalid command arguments.')
  return { command: value.command, cwd, timeoutMs }
}

function helperEnvironment(parent: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const source = new Map(Object.entries(parent).map(([key, value]) => [key.toUpperCase(), value]))
  const root = source.get('SYSTEMROOT')
  if (root === undefined || !absolute(root) || /[\0\r\n]/u.test(root)) throw new Error('Windows is unavailable.')
  const system32 = path.win32.join(root, 'System32')
  const entries = [system32], seen = new Set([system32.toLowerCase()])
  // Preserve installed tools while dropping relative/empty PATH entries. Full
  // CMD authority intentionally permits those programs; PATH is not a sandbox.
  for (let entry of (source.get('PATH') ?? '').split(';')) {
    entry = entry.trim().replace(/^"|"$/gu, '')
    if (!entry || !absolute(entry) || /[\0\r\n]/u.test(entry) || seen.has(entry.toLowerCase())) continue
    entries.push(entry); seen.add(entry.toLowerCase())
  }
  const env: NodeJS.ProcessEnv = {
    SystemRoot: root, WINDIR: root, PATH: entries.join(';'), ComSpec: path.win32.join(system32, 'cmd.exe'),
    PATHEXT: '.COM;.EXE;.BAT;.CMD', NoDefaultCurrentDirectoryInExePath: '1',
    PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PYTHONNOUSERSITE: '1',
  }
  for (const name of ['TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432']) {
    const value = source.get(name)
    if (value !== undefined && absolute(value) && !/[\0\r\n]/u.test(value)) env[name] = value
  }
  for (const name of ['COMPUTERNAME', 'USERNAME', 'USERDOMAIN', 'USERDNSDOMAIN', 'HOMEDRIVE', 'HOMEPATH',
    'SYSTEMDRIVE', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432', 'NUMBER_OF_PROCESSORS', 'OS']) {
    const value = source.get(name)
    if (validText(value, 2048) && !/[\r\n]/u.test(value)) env[name] = value
  }
  return env
}

function failed(error: NonNullable<WorkToolResult['error']>, args?: CommandArguments): WorkToolResult {
  const cancelled = error === 'cancelled', timeout = error === 'timeout'
  return { status: cancelled ? 'cancelled' : error === 'permission_denied' ? 'denied' : 'failed', error,
    text: cancelled ? 'CMD was cancelled.' : timeout ? 'CMD timed out.' : 'CMD could not complete.',
    ...(args === undefined ? {} : { command: {
      command: args.command, cwd: args.cwd, stdout: '', stderr: '', exitCode: null,
      termination: cancelled ? 'cancelled' as const : timeout ? 'timeout' as const : 'spawn_failed' as const, truncated: false,
    } }),
  }
}

/** Execute only already-approved ask/full requests and retain the lease until the helper physically closes. */
export function createWorkCommandNativeRunner(
  host: WorkCommandNativeHost, injected: Partial<WorkCommandNativeDependencies> = {},
): (request: WorkToolCall, signal: AbortSignal) => Promise<WorkToolResult> {
  const dependencies: WorkCommandNativeDependencies = {
    platform: process.platform, environment: process.env, spawnProcess: spawn, cancellationGraceMs: 5000, ...injected,
  }
  let active = false
  return async (request, signal) => {
    if (active) return failed('permission_denied')
    if (!['ask', 'full'].includes(request.options?.permission)) return failed('permission_denied')
    let args: CommandArguments
    try { args = commandArguments(request, host.defaultCwd) } catch { return failed('invalid_request') }
    if (signal.aborted || !host.isCurrent(request)) return failed('cancelled', args)
    if (dependencies.platform !== 'win32') return failed('unavailable', args)
    let environment: NodeJS.ProcessEnv
    try {
      environment = helperEnvironment(dependencies.environment)
      if (!absolute(host.pythonExecutable) || !absolute(host.projectRoot)) return failed('unavailable', args)
    } catch { return failed('unavailable', args) }
    active = true
    try {
      return await new Promise<WorkToolResult>((resolve) => {
        let child: ChildProcess
        try {
          child = dependencies.spawnProcess(host.pythonExecutable, ['-B', '-u', '-s', '-m', 'work_tools.command_runner'], {
            cwd: host.projectRoot, env: environment, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'],
          })
        } catch { resolve(failed('unavailable', args)); return }
        let failure: NonNullable<WorkToolResult['error']> | null = null
        let output = '', bytes = 0, stderrBytes = 0, closed = false
        let force: ReturnType<typeof setTimeout> | null = null
        const stop = (reason: NonNullable<WorkToolResult['error']>): void => {
          if (closed) return
          failure ??= reason
          // EOF is a private cancellation signal, never command stdin. Kill is
          // the fallback only; closing the helper's Job handle kills ordinary
          // CMD descendants. Neither signal nor timeout releases our lease.
          child.stdin?.end()
          force ??= setTimeout(() => { try { child.kill() } catch { /* Keep waiting for physical close. */ } }, dependencies.cancellationGraceMs)
        }
        const abort = (): void => stop('cancelled')
        const deadline = setTimeout(() => stop('timeout'), args.timeoutMs + dependencies.cancellationGraceMs)
        // Navigation may revoke ownership without aborting the bridge signal.
        const ownership = setInterval(() => { if (!host.isCurrent(request)) stop('cancelled') }, 100)
        signal.addEventListener('abort', abort, { once: true })
        child.once('error', () => { failure ??= 'unavailable' })
        child.stdin?.on('error', () => stop('failed'))
        child.stdout?.setEncoding('utf8')
        child.stdout?.on('data', (chunk: string) => {
          bytes += Buffer.byteLength(chunk, 'utf8')
          if (bytes > OUTPUT_LIMIT) stop('failed')
          else output += chunk
        })
        child.stderr?.on('data', (chunk: Buffer) => {
          // Helper diagnostics are private. Drain continuously without keeping
          // exception text; CMD's intentional stderr lives in the JSON receipt.
          stderrBytes += chunk.byteLength
          if (stderrBytes > OUTPUT_LIMIT) stop('failed')
        })
        child.once('close', (code) => {
          closed = true
          clearTimeout(deadline); clearInterval(ownership)
          if (force !== null) clearTimeout(force)
          signal.removeEventListener('abort', abort)
          if (signal.aborted || !host.isCurrent(request)) failure = 'cancelled'
          if (failure !== null) { resolve(failed(failure, args)); return }
          try {
            if (code !== 0 || !output.endsWith('\n') || output.trim().includes('\n')) throw new Error('Invalid helper response.')
            const result = parseWorkToolResult(JSON.parse(output))
            if (result.command?.command !== args.command || result.command.cwd !== args.cwd) throw new Error('Mismatched command receipt.')
            resolve(result)
          } catch { resolve(failed('failed', args)) }
        })
        if (signal.aborted || !host.isCurrent(request)) abort()
        else if (child.stdin === null || child.stdout === null || child.stderr === null) stop('unavailable')
        else child.stdin.write(`${JSON.stringify(args)}\n`, 'utf8')
      })
    } finally { active = false }
  }
}
