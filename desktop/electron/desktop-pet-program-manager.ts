/**
 * Own and reconcile one opt-in external Desktop Pet program process.
 *
 * Purchased companion programs remain in the user's chosen folder. This
 * module launches an already-validated executable in place and deliberately
 * never reads, copies, or writes the program's assets or configuration.
 */

import {
  execFile,
  spawn,
  type ChildProcess,
  type SpawnOptions,
} from 'node:child_process'
import path from 'node:path'

/** Runtime states exposed to Electron Main and, through it, the Renderer. */
export type DesktopPetProgramRuntime =
  | 'absent'
  | 'loading'
  | 'visible'
  | 'failed'

/** Identify one validated external Desktop Pet program without owning it. */
export interface DesktopPetProgramDescriptor {
  /** Stable scanner identity used to avoid restarting the same selection. */
  readonly id: string
  /** Existing directory used as the child process working directory. */
  readonly programDirectory: string
  /** Absolute path to the program executable selected by the scanner. */
  readonly executablePath: string
}

/** Receive safe process state without exposing a native process handle. */
export type DesktopPetProgramStateSink = (
  runtime: DesktopPetProgramRuntime,
  warning: string | null,
) => void

/** Replace native process primitives in deterministic unit tests. */
export interface DesktopPetProgramManagerDependencies {
  /** Start the exact executable with explicit non-shell process options. */
  readonly spawnProgram: (
    executablePath: string,
    args: readonly string[],
    options: SpawnOptions,
  ) => ChildProcess
  /** Request termination and settle only after the owned child exits. */
  readonly stopProgram: (
    child: ChildProcess,
    terminateProcessTree: (pid: number) => Promise<void>,
  ) => Promise<void>
  /** Terminate the process tree rooted at one exact manager-owned PID. */
  readonly terminateProcessTree: (pid: number) => Promise<void>
  /** Bound an operation while preserving any rejection from that operation. */
  readonly runWithinDeadline: (
    operation: Promise<void>,
    timeoutMs: number,
  ) => Promise<boolean>
}

interface OwnedDesktopPetProgram {
  readonly descriptor: DesktopPetProgramDescriptor
  readonly child: ChildProcess
  stopping: boolean
  unhealthy: boolean
}

const PROGRAM_START_TIMEOUT_MS = 10_000
const PROGRAM_STOP_TIMEOUT_MS = 10_000
const WINDOWS_TASKKILL_TIMEOUT_MS = 8_000
const PROGRAM_START_FAILURE = 'Desktop Pet program could not be started.'
const PROGRAM_START_TIMEOUT = 'Desktop Pet program did not start in time.'
const PROGRAM_STOP_FAILURE = 'Desktop Pet program could not be closed safely.'
const PROGRAM_STOP_TIMEOUT = 'Desktop Pet program did not close in time.'
const PROGRAM_SELECTION_MISSING = 'Choose a detected Desktop Pet program first.'
const PROGRAM_EXITED = 'Desktop Pet program closed unexpectedly.'

function copySafeEnvironmentValue(
  target: NodeJS.ProcessEnv,
  name: string,
  requireAbsolutePath = false,
): void {
  const value = process.env[name]
  if (
    value === undefined
    || value.length === 0
    || /[\0\r\n]/u.test(value)
    || (requireAbsolutePath && !path.isAbsolute(value))
  ) {
    return
  }
  target[name] = value
}

function windowsSystemRoot(): string {
  const systemRoot = process.env.SystemRoot ?? process.env.WINDIR
  if (
    systemRoot === undefined
    || /[\0\r\n]/u.test(systemRoot)
    || !path.win32.isAbsolute(systemRoot)
  ) {
    throw new Error('Windows system root is unavailable.')
  }
  return systemRoot
}

function desktopPetProgramEnvironment(): NodeJS.ProcessEnv {
  // A companion program has the same Windows-user filesystem authority as
  // Elysia, but it does not need API keys or development credentials inherited
  // from the terminal that launched Elysia. Pass only ordinary OS/profile
  // paths required by a native desktop application.
  const environment: NodeJS.ProcessEnv = {}
  if (process.platform === 'win32') {
    const systemRoot = windowsSystemRoot()
    environment.SystemRoot = systemRoot
    environment.WINDIR = systemRoot
    environment.PATH = path.win32.join(systemRoot, 'System32')
    environment.ComSpec = path.win32.join(
      systemRoot,
      'System32',
      'cmd.exe',
    )
    for (const name of [
      'APPDATA',
      'LOCALAPPDATA',
      'PROGRAMDATA',
      'TEMP',
      'TMP',
      'USERPROFILE',
    ]) {
      copySafeEnvironmentValue(environment, name, true)
    }
    return environment
  }
  // The reviewed program is Windows-only. This small fallback keeps injected
  // process tests and unsupported platforms deterministic without forwarding
  // arbitrary parent-process secrets.
  for (const name of ['HOME', 'LANG', 'LC_ALL', 'PATH', 'TMPDIR']) {
    copySafeEnvironmentValue(environment, name)
  }
  return environment
}

function descriptorsMatch(
  left: DesktopPetProgramDescriptor,
  right: DesktopPetProgramDescriptor,
): boolean {
  return left.id === right.id
    && left.programDirectory === right.programDirectory
    && left.executablePath === right.executablePath
}

function waitForProgramSpawn(child: ChildProcess): Promise<void> {
  // A native PID proves spawn already succeeded before listeners were added.
  // Failed Windows launches have no PID and instead emit the ``error`` event.
  if (typeof child.pid === 'number' && child.pid > 0) {
    return Promise.resolve()
  }
  return new Promise((resolve, reject) => {
    const cleanup = (): void => {
      child.off('spawn', handleSpawn)
      child.off('error', handleError)
    }
    const handleSpawn = (): void => {
      cleanup()
      resolve()
    }
    const handleError = (error: Error): void => {
      cleanup()
      reject(error)
    }
    child.once('spawn', handleSpawn)
    child.once('error', handleError)
  })
}

function spawnDesktopPetProgram(
  executablePath: string,
  args: readonly string[],
  options: SpawnOptions,
): ChildProcess {
  return spawn(executablePath, args, options)
}

function terminateOwnedProcessTree(pid: number): Promise<void> {
  if (!Number.isSafeInteger(pid) || pid <= 0) {
    return Promise.reject(new Error('Desktop Pet process PID is invalid.'))
  }
  if (process.platform !== 'win32') {
    try {
      // The non-Windows fallback remains PID-specific and never searches by
      // executable name, preserving the same ownership boundary as Windows.
      process.kill(pid, 'SIGTERM')
      return Promise.resolve()
    } catch (error) {
      return Promise.reject(error)
    }
  }

  let systemRoot: string
  try {
    systemRoot = windowsSystemRoot()
  } catch (error) {
    return Promise.reject(error)
  }
  const taskkillPath = path.win32.join(
    systemRoot,
    'System32',
    'taskkill.exe',
  )
  return new Promise((resolve, reject) => {
    // ``/PID <owned PID> /T`` closes only the recorded root and descendants.
    // There is intentionally no ``/IM`` name match and no broad enumeration,
    // so manually started copies of the same purchased program are untouched.
    execFile(
      taskkillPath,
      ['/PID', String(pid), '/T'],
      {
        windowsHide: true,
        timeout: WINDOWS_TASKKILL_TIMEOUT_MS,
      },
      (error) => {
        if (error === null) {
          resolve()
          return
        }
        reject(error)
      },
    )
  })
}

function stopDesktopPetProgram(
  child: ChildProcess,
  terminateProcessTree: (pid: number) => Promise<void>,
): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve()
  }
  const pid = child.pid
  if (typeof pid !== 'number' || !Number.isSafeInteger(pid) || pid <= 0) {
    return Promise.reject(new Error('Desktop Pet process PID is unavailable.'))
  }
  return new Promise((resolve, reject) => {
    let settled = false
    const cleanup = (): void => {
      child.off('exit', handleExit)
      child.off('error', handleError)
    }
    const finish = (error?: unknown): void => {
      if (settled) {
        return
      }
      settled = true
      cleanup()
      if (error === undefined) {
        resolve()
        return
      }
      reject(error)
    }
    const handleExit = (): void => {
      finish()
    }
    const handleError = (error: Error): void => {
      finish(error)
    }
    child.once('exit', handleExit)
    child.once('error', handleError)
    void terminateProcessTree(pid).then(
      () => {
        // taskkill may finish just before Node delivers the root's exit event.
        // Resolve here only when the ChildProcess already confirms termination.
        if (child.exitCode !== null || child.signalCode !== null) {
          finish()
        }
      },
      (error: unknown) => {
        // An exit event can win the race with taskkill's non-zero result when
        // the process closes itself. Treat that confirmed exit as success.
        if (child.exitCode !== null || child.signalCode !== null) {
          finish()
          return
        }
        finish(error)
      },
    )
  })
}

function runDesktopPetOperationWithin(
  operation: Promise<void>,
  timeoutMs: number,
): Promise<boolean> {
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    return Promise.reject(new Error('Desktop Pet process deadline is invalid.'))
  }
  return new Promise((resolve, reject) => {
    let settled = false
    const finish = (completed: boolean, error?: unknown): void => {
      if (settled) {
        return
      }
      settled = true
      clearTimeout(timer)
      if (error !== undefined) {
        reject(error)
        return
      }
      resolve(completed)
    }
    const timer = setTimeout(() => { finish(false) }, timeoutMs)
    void operation.then(
      () => { finish(true) },
      (error: unknown) => { finish(false, error) },
    )
  })
}

const DEFAULT_DEPENDENCIES: DesktopPetProgramManagerDependencies = {
  spawnProgram: spawnDesktopPetProgram,
  stopProgram: stopDesktopPetProgram,
  terminateProcessTree: terminateOwnedProcessTree,
  runWithinDeadline: runDesktopPetOperationWithin,
}

/**
 * Serialize external Desktop Pet program transitions and own at most one child.
 *
 * A selected program is never launched until the previously owned child has
 * stopped. Failed or timed-out stops retain ownership and block replacement so
 * Elysia cannot accidentally leave two companion programs running.
 */
export class DesktopPetProgramManager {
  readonly #onState: DesktopPetProgramStateSink
  readonly #dependencies: DesktopPetProgramManagerDependencies
  #owned: OwnedDesktopPetProgram | null = null
  #pending: Promise<void> = Promise.resolve()
  #shutdownRequested = false
  #lastRuntime: DesktopPetProgramRuntime | null = null
  #lastWarning: string | null = null

  /** Create a manager around one state sink and optional test primitives. */
  constructor(
    onState: DesktopPetProgramStateSink,
    dependencies: Partial<DesktopPetProgramManagerDependencies> = {},
  ) {
    this.#onState = onState
    this.#dependencies = { ...DEFAULT_DEPENDENCIES, ...dependencies }
  }

  /**
   * Make the owned process match the latest selected program and visibility.
   *
   * Calls are admitted in order. Switching waits for the old child to exit;
   * disabling stops it; and an enabled selection launches in its own folder so
   * the original program continues to own and preserve its settings files.
   */
  reconcile(
    program: DesktopPetProgramDescriptor | null,
    shouldRun: boolean,
  ): Promise<void> {
    const snapshot = program === null ? null : { ...program }
    return this.#enqueue(async () => {
      if (this.#shutdownRequested || !shouldRun) {
        if (!(await this.#stopOwnedProgram())) {
          throw new Error(PROGRAM_STOP_FAILURE)
        }
        return
      }
      if (snapshot === null) {
        if (await this.#stopOwnedProgram()) {
          this.#emit('failed', PROGRAM_SELECTION_MISSING)
        }
        return
      }
      if (
        this.#owned !== null
        && !this.#owned.unhealthy
        && descriptorsMatch(this.#owned.descriptor, snapshot)
      ) {
        this.#emit('visible', null)
        return
      }
      if (!(await this.#stopOwnedProgram())) {
        throw new Error(PROGRAM_STOP_FAILURE)
      }
      if (this.#shutdownRequested) {
        this.#emit('absent', null)
        return
      }
      await this.#startProgram(snapshot)
    })
  }

  /** Stop the currently owned program without preventing a later restart. */
  stop(): Promise<void> {
    return this.#enqueue(async () => {
      if (!(await this.#stopOwnedProgram())) {
        throw new Error(PROGRAM_STOP_FAILURE)
      }
    })
  }

  /** Permanently prevent new launches and boundedly stop the owned program. */
  shutdown(): Promise<void> {
    this.#shutdownRequested = true
    return this.#enqueue(async () => {
      if (!(await this.#stopOwnedProgram())) {
        throw new Error(PROGRAM_STOP_FAILURE)
      }
    })
  }

  #enqueue(operation: () => Promise<void>): Promise<void> {
    const admitted = this.#pending.then(operation, operation)
    this.#pending = admitted.catch(() => {})
    return admitted
  }

  async #startProgram(descriptor: DesktopPetProgramDescriptor): Promise<void> {
    this.#emit('loading', null)
    let child: ChildProcess
    try {
      child = this.#dependencies.spawnProgram(
        descriptor.executablePath,
        [],
        {
          cwd: descriptor.programDirectory,
          env: desktopPetProgramEnvironment(),
          shell: false,
          detached: false,
          windowsHide: false,
          stdio: 'ignore',
        },
      )
    } catch {
      this.#emit('failed', PROGRAM_START_FAILURE)
      return
    }

    const owned: OwnedDesktopPetProgram = {
      descriptor,
      child,
      stopping: false,
      unhealthy: false,
    }
    this.#owned = owned
    let started: boolean
    try {
      started = await this.#dependencies.runWithinDeadline(
        waitForProgramSpawn(child),
        PROGRAM_START_TIMEOUT_MS,
      )
    } catch {
      if (this.#owned === owned) {
        this.#owned = null
      }
      this.#emit('failed', PROGRAM_START_FAILURE)
      return
    }
    if (!started) {
      if (await this.#stopOwnedProgram()) {
        this.#emit('failed', PROGRAM_START_TIMEOUT)
      }
      return
    }
    if (this.#owned !== owned) {
      return
    }

    child.once('exit', () => {
      if (this.#owned !== owned) {
        return
      }
      this.#owned = null
      this.#emit(
        owned.stopping || this.#shutdownRequested ? 'absent' : 'failed',
        owned.stopping || this.#shutdownRequested ? null : PROGRAM_EXITED,
      )
    })
    child.once('error', () => {
      if (this.#owned !== owned) {
        return
      }
      // ChildProcess can report an operation error while its OS process is
      // still alive. Retain exact ownership until exit is confirmed so retry
      // or replacement must stop this PID before launching another program.
      owned.unhealthy = true
      if (child.exitCode !== null || child.signalCode !== null) {
        this.#owned = null
      }
      this.#emit('failed', PROGRAM_EXITED)
    })
    // A very short-lived native program can exit after Node reports a PID but
    // before these lifecycle listeners are installed. Check the retained
    // status after listener registration so that process cannot be reported as
    // visible forever merely because its one exit event was already delivered.
    if (child.exitCode !== null || child.signalCode !== null) {
      if (this.#owned === owned) {
        this.#owned = null
        this.#emit('failed', PROGRAM_EXITED)
      }
      return
    }
    this.#emit('visible', null)
  }

  async #stopOwnedProgram(): Promise<boolean> {
    const owned = this.#owned
    if (owned === null) {
      this.#emit('absent', null)
      return true
    }
    owned.stopping = true
    this.#emit('loading', null)
    let stopped: boolean
    try {
      stopped = await this.#dependencies.runWithinDeadline(
        this.#dependencies.stopProgram(
          owned.child,
          this.#dependencies.terminateProcessTree,
        ),
        PROGRAM_STOP_TIMEOUT_MS,
      )
    } catch {
      this.#emit('failed', PROGRAM_STOP_FAILURE)
      return false
    }
    if (!stopped) {
      this.#emit('failed', PROGRAM_STOP_TIMEOUT)
      return false
    }
    if (this.#owned === owned) {
      this.#owned = null
    }
    this.#emit('absent', null)
    return true
  }

  #emit(runtime: DesktopPetProgramRuntime, warning: string | null): void {
    if (runtime === this.#lastRuntime && warning === this.#lastWarning) {
      return
    }
    this.#lastRuntime = runtime
    this.#lastWarning = warning
    this.#onState(runtime, warning)
  }
}
