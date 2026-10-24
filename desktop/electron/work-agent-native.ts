/** Admit Work desktop tools through native dialogs and CMD calls through inline, turn-bound approval. */

import { localizeNativeDialog, nativeText } from './native-locale.js'
import { dialog, type BrowserWindow, type MessageBoxOptions } from 'electron'
import { spawn, type ChildProcess, type SpawnOptions } from 'node:child_process'
import { lstat, realpath } from 'node:fs/promises'
import path from 'node:path'
import { parseWorkCommandArguments, type WorkCommandArguments, type WorkCommandApprovalOutcome, type WorkToolCall, type WorkToolResult } from './work-agent-protocol.js'

/** Main retains canonical ownership of the originating conversation and its turn. */
export interface WorkAgentNativeHost {
  /** Accept only the still-current Work turn, excluding that turn itself from competing-work checks. */
  isCurrent(request: WorkToolCall): boolean
  /** Return the trusted application window that owns a native approval dialog. */
  getWindow(): BrowserWindow
  /** Supply Main's user-home default rather than inheriting an application's installation directory. */
  defaultCommandCwd?: string
  /** Run a reviewed CMD request and settle only after its managed process tree has closed. */
  runCommand?(request: WorkToolCall, signal: AbortSignal): Promise<WorkToolResult>
  /** Publish the detached CMD request inline and wait for Main to validate the user's exact decision. */
  approveCommand?(request: WorkToolCall, signal: AbortSignal): Promise<WorkCommandApprovalOutcome>
}

/** Replace disk, dialogs, and child processes without changing real applications or audio in tests. */
export interface WorkAgentNativeDependencies {
  readonly platform: NodeJS.Platform
  readonly systemRoot: string | undefined
  /** Inspect a fixed system path, including its resolved identity, without following an accepted alias. */
  inspectPath(target: string): Promise<{ file: boolean; directory: boolean; link: boolean; realPath: string }>
  /** Start a known executable with fixed arguments; never interpret a model-provided command line. */
  spawnProcess(executable: string, args: readonly string[], options: SpawnOptions): ChildProcess
  /** Ask for approval of one concrete effect and close the dialog when its request is aborted. */
  confirm(window: BrowserWindow, options: MessageBoxOptions): Promise<{ response: number }>
}

const APPLICATIONS = {
  notepad: { name: 'Notepad', parts: ['System32', 'notepad.exe'] },
  calculator: { name: 'Calculator', parts: ['System32', 'calc.exe'] },
  paint: { name: 'Paint', parts: ['System32', 'mspaint.exe'] },
  explorer: { name: 'File Explorer', parts: ['explorer.exe'] },
} as const
type ApplicationId = keyof typeof APPLICATIONS
type AudioState = { endpointId: string; level: number; muted: boolean }
type AudioCommand = { level: number; endpointId: string } | Record<string, never>
const HELPER_TIMEOUT_MS = 20_000
const HELPER_OUTPUT_LIMIT = 8_192

// The command line contains only this source-controlled program. Typed data is
// sent over stdin after READY and a fresh owner check. Core Audio addresses the
// default multimedia output directly, avoiding focus-dependent keyboard input.
// The reviewed endpoint ID is checked again before setting volume, and changing
// level deliberately preserves mute. HRESULTs and readback determine success.
const AUDIO_HELPER = String.raw`
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class AudioEnumerator {}
[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEnumerator {
  [PreserveSig] int EnumAudioEndpoints(int flow, uint state, out IntPtr devices);
  [PreserveSig] int GetDefaultAudioEndpoint(int flow, int role, out IAudioDevice device);
  [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IAudioDevice device);
  [PreserveSig] int RegisterEndpointNotificationCallback(IntPtr callback);
  [PreserveSig] int UnregisterEndpointNotificationCallback(IntPtr callback);
}
[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioDevice {
  [PreserveSig] int Activate(ref Guid iid, int context, IntPtr parameters, [MarshalAs(UnmanagedType.IUnknown)] out object value);
  [PreserveSig] int OpenPropertyStore(int mode, out IntPtr properties);
  [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
  [PreserveSig] int GetState(out uint state);
}
[ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IEndpointVolume {
  [PreserveSig] int RegisterControlChangeNotify(IntPtr callback);
  [PreserveSig] int UnregisterControlChangeNotify(IntPtr callback);
  [PreserveSig] int GetChannelCount(out uint count);
  [PreserveSig] int SetMasterVolumeLevel(float level, ref Guid context);
  [PreserveSig] int SetMasterVolumeLevelScalar(float level, ref Guid context);
  [PreserveSig] int GetMasterVolumeLevel(out float level);
  [PreserveSig] int GetMasterVolumeLevelScalar(out float level);
  [PreserveSig] int SetChannelVolumeLevel(uint channel, float level, ref Guid context);
  [PreserveSig] int SetChannelVolumeLevelScalar(uint channel, float level, ref Guid context);
  [PreserveSig] int GetChannelVolumeLevel(uint channel, out float level);
  [PreserveSig] int GetChannelVolumeLevelScalar(uint channel, out float level);
  [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool muted, ref Guid context);
  [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool muted);
}
public sealed class WorkAudioState {
  public string endpointId;
  public int level;
  public bool muted;
}
public static class WorkAudio {
  public static WorkAudioState Run(int level, string expected) {
    if(level < -1 || level > 100) throw new ArgumentOutOfRangeException("level");
    IAudioEnumerator enumerator=null; IAudioDevice device=null; object volumeObject=null;
    try {
      enumerator=(IAudioEnumerator)new AudioEnumerator();
      Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(0,1,out device));
      string id; Marshal.ThrowExceptionForHR(device.GetId(out id));
      if(level>=0 && !String.Equals(id,expected,StringComparison.Ordinal)) throw new InvalidOperationException("ELYSIA_ENDPOINT_CHANGED");
      Guid iid=new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
      Marshal.ThrowExceptionForHR(device.Activate(ref iid,23,IntPtr.Zero,out volumeObject));
      var volume=(IEndpointVolume)volumeObject;
      if(level>=0) {
        Guid context=Guid.Empty;
        Marshal.ThrowExceptionForHR(volume.SetMasterVolumeLevelScalar(level/100.0f,ref context));
      }
      float scalar; bool muted;
      Marshal.ThrowExceptionForHR(volume.GetMasterVolumeLevelScalar(out scalar));
      Marshal.ThrowExceptionForHR(volume.GetMute(out muted));
      return new WorkAudioState {endpointId=id,level=(int)Math.Round(scalar*100),muted=muted};
    } finally {
      if(volumeObject!=null) Marshal.ReleaseComObject(volumeObject);
      if(device!=null) Marshal.ReleaseComObject(device);
      if(enumerator!=null) Marshal.ReleaseComObject(enumerator);
    }
  }
}
'@
[Console]::WriteLine('READY')
[Console]::Out.Flush()
$line = [Console]::ReadLine()
if ($null -eq $line -or $line.Length -gt 2048) { throw 'Invalid request' }
$request = $line | ConvertFrom-Json
$names = @($request.PSObject.Properties | ForEach-Object { $_.Name })
$level = -1
$endpoint = ''
if ($names.Count -gt 0) {
  if ($names.Count -ne 2 -or $names -notcontains 'level' -or $names -notcontains 'endpointId' -or
      ($request.level -isnot [int] -and $request.level -isnot [long]) -or $request.level -lt 0 -or $request.level -gt 100 -or
      $request.endpointId -isnot [string] -or $request.endpointId.Length -gt 1024) { throw 'Invalid request' }
  $level = $request.level
  $endpoint = $request.endpointId
}
$result = [WorkAudio]::Run($level, $endpoint)
[Console]::WriteLine(($result | ConvertTo-Json -Compress))
[Console]::Out.Flush()
} catch {
  $errorCode = if ($_.Exception.ToString().Contains('ELYSIA_ENDPOINT_CHANGED')) { 'endpoint_changed' } else { 'failed' }
  [Console]::WriteLine((@{error=$errorCode} | ConvertTo-Json -Compress))
  [Console]::Out.Flush()
  exit 1
}
`

class NativeFailure extends Error {
  constructor(readonly code: NonNullable<WorkToolResult['error']>, message: string) { super(message) }
}

function result(status: WorkToolResult['status'], text: string, error: WorkToolResult['error'] = null): WorkToolResult {
  return { status, text, error }
}

function systemRoot(dependencies: WorkAgentNativeDependencies): string {
  const root = dependencies.systemRoot
  if (dependencies.platform !== 'win32' || root === undefined || !/^[A-Za-z]:\\/u.test(root)
    || path.win32.normalize(root) !== root || /[<>"|?*]/u.test(root) || root.slice(2).includes(':')
    || [...root].some((character) => character.codePointAt(0)! < 32)) {
    throw new NativeFailure('unavailable', 'These desktop tools require a trusted local Windows installation.')
  }
  return root
}

async function systemFile(
  dependencies: WorkAgentNativeDependencies, parts: readonly string[], check: () => void,
): Promise<string> {
  const root = systemRoot(dependencies)
  for (let index = 0; index <= parts.length; index += 1) {
    const target = path.win32.join(root, ...parts.slice(0, index))
    let inspected: Awaited<ReturnType<WorkAgentNativeDependencies['inspectPath']>>
    try { inspected = await dependencies.inspectPath(target) }
    catch { throw new NativeFailure('unavailable', 'The requested Windows application is not installed in its trusted location.') }
    check()
    if (inspected.link || (index === parts.length ? !inspected.file : !inspected.directory)
      || inspected.realPath.toLowerCase() !== path.win32.resolve(target).toLowerCase()) {
      throw new NativeFailure('unavailable', 'The requested Windows application has an unsupported path or link.')
    }
  }
  return path.win32.join(root, ...parts)
}

function parseArguments(request: WorkToolCall): ApplicationId | number | WorkCommandArguments | null {
  const args = request.arguments
  if (args === null || typeof args !== 'object' || Array.isArray(args)) throw new NativeFailure('invalid_request', 'Invalid tool arguments.')
  const keys = Object.keys(args)
  if (request.tool === 'run_command') {
    try { return parseWorkCommandArguments(args) }
    catch { throw new NativeFailure('invalid_request', 'Use a nonempty single-line CMD command; join operations with & or &&. Supply a drive-qualified absolute working directory and a timeout from 1000 to 120000 milliseconds.') }
  }
  if ((request.tool === 'list_apps' || request.tool === 'get_volume') && keys.length === 0) return null
  if (request.tool === 'open_app' && keys.length === 1 && keys[0] === 'application'
    && typeof args.application === 'string' && Object.hasOwn(APPLICATIONS, args.application)) return args.application as ApplicationId
  if (request.tool === 'set_volume' && keys.length === 1 && keys[0] === 'level'
    && typeof args.level === 'number' && Number.isInteger(args.level) && args.level >= 0 && args.level <= 100) return args.level
  throw new NativeFailure('invalid_request', 'Use an available application ID or an integer volume from 0 to 100; extra arguments are not accepted.')
}

function audioState(value: unknown): AudioState {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new NativeFailure('failed', 'The audio helper returned an invalid result.')
  const record = value as Record<string, unknown>
  if (record.error === 'endpoint_changed') throw new NativeFailure('unavailable', 'The audio output changed. Volume was not adjusted; request the operation again.')
  if (Object.keys(record).length !== 3 || typeof record.endpointId !== 'string' || record.endpointId.length === 0
    || record.endpointId.length > 1024 || typeof record.level !== 'number' || !Number.isInteger(record.level)
    || record.level < 0 || record.level > 100 || typeof record.muted !== 'boolean') {
    throw new NativeFailure('failed', 'Windows could not read or change the default audio output.')
  }
  return { endpointId: record.endpointId, level: record.level, muted: record.muted }
}

async function runAudio(
  dependencies: WorkAgentNativeDependencies, command: AudioCommand, signal: AbortSignal,
  check: () => void, dispatch: () => void,
): Promise<AudioState> {
  const executable = await systemFile(dependencies, ['System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'], check)
  check()
  const root = systemRoot(dependencies)
  return new Promise<AudioState>((resolve, reject) => {
    let child: ChildProcess
    try {
      child = dependencies.spawnProcess(executable, ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
        Buffer.from(AUDIO_HELPER, 'utf16le').toString('base64')], {
        windowsHide: true, shell: false, cwd: path.win32.join(root, 'System32'), stdio: ['pipe', 'pipe', 'ignore'],
        env: { SystemRoot: root, WINDIR: root, PATH: path.win32.join(root, 'System32'), TEMP: process.env.TEMP, TMP: process.env.TMP },
      })
    } catch { reject(new NativeFailure('unavailable', 'The Windows audio helper could not start.')); return }
    let failure: NativeFailure | null = null
    let received = 0
    let buffer = ''
    let ready = false
    let state: AudioState | null = null
    const stop = (error: NativeFailure) => {
      failure ??= error
      try { child.kill() } catch { /* A failed termination request still cannot release a live helper. */ }
      // Abort requests termination but does not release the physical lease.
      // Only close proves that this helper can no longer adjust the endpoint.
    }
    const abort = () => stop(new NativeFailure('cancelled', 'The audio operation was cancelled.'))
    const timeout = setTimeout(() => stop(new NativeFailure('timeout', 'The Windows audio helper timed out.')), HELPER_TIMEOUT_MS)
    signal.addEventListener('abort', abort, { once: true })
    child.once('error', () => { failure ??= new NativeFailure('unavailable', 'The Windows audio helper could not start.') })
    child.stdin?.on('error', () => stop(new NativeFailure('failed', 'The Windows audio helper stopped accepting its request.')))
    child.stdout?.on('data', (chunk: Buffer) => {
      if (failure !== null) return
      received += chunk.byteLength
      if (received > HELPER_OUTPUT_LIMIT) { stop(new NativeFailure('failed', 'The audio helper exceeded its output limit.')); return }
      buffer += chunk.toString('utf8')
      let newline: number
      while ((newline = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newline).replace(/\r$/u, '')
        buffer = buffer.slice(newline + 1)
        try {
          if (!ready && line === 'READY') {
            ready = true
            check()
            dispatch()
            child.stdin!.end(`${JSON.stringify(command)}\n`, 'utf8')
          } else if (ready && state === null) {
            state = audioState(JSON.parse(line))
          } else throw new NativeFailure('failed', 'The audio helper returned an unexpected response.')
        } catch (error) {
          stop(error instanceof NativeFailure ? error : new NativeFailure('failed', 'The audio helper returned an invalid response.'))
          return
        }
      }
    })
    child.once('close', (code) => {
      clearTimeout(timeout)
      signal.removeEventListener('abort', abort)
      if (failure !== null) reject(failure)
      else if (code !== 0 || state === null || buffer.trim() !== '') reject(new NativeFailure('failed', 'The Windows audio operation did not complete.'))
      else resolve(state)
    })
    if (child.stdin === null || child.stdout === null) stop(new NativeFailure('failed', 'The Windows audio helper has no private data channel.'))
    else if (signal.aborted) abort()
  })
}

/** Create a serial native executor; its promise settles only after an admitted helper has physically closed. */
export function createWorkAgentNativeTools(
  host: WorkAgentNativeHost, injected: Partial<WorkAgentNativeDependencies> = {},
): (request: WorkToolCall, signal: AbortSignal) => Promise<WorkToolResult> {
  const dependencies: WorkAgentNativeDependencies = {
    platform: process.platform, systemRoot: process.env.SystemRoot,
    inspectPath: async (target) => {
      const [stats, resolved] = await Promise.all([lstat(target), realpath(target)])
      return { file: stats.isFile(), directory: stats.isDirectory(), link: stats.isSymbolicLink(), realPath: resolved }
    },
    spawnProcess: spawn,
    confirm: (window, options) => dialog.showMessageBox(window, options),
    ...injected,
  }
  let active = false
  return async (request, signal) => {
    if (active) return result('denied', 'Another native Work operation is still finishing.', 'permission_denied')
    active = true
    let dispatched = false
    const check = () => {
      if (signal.aborted || !host.isCurrent(request)) throw new NativeFailure('cancelled', 'This Work request is no longer active.')
    }
    const approve = async (message: string, detail: string) => {
      check()
      if (request.options.permission !== 'ask') return
      const response = await dependencies.confirm(host.getWindow(), localizeNativeDialog({
        type: 'question', title: 'Allow Work action?', message, detail,
        buttons: ['Cancel', 'Allow'], defaultId: 0, cancelId: 0, noLink: true, signal,
      }))
      check()
      if (response.response !== 1) throw new NativeFailure('permission_denied', 'The action was not approved.')
    }
    try {
      check()
      const argument = parseArguments(request)
      const permission = request.options?.permission
      const writing = request.tool === 'open_app' || request.tool === 'set_volume'
      if (!['none', 'read', 'ask', 'auto', 'full'].includes(permission) || permission === 'none' || (permission === 'read' && writing)
        || request.tool === 'run_command' && permission !== 'ask' && permission !== 'full') {
        return result('denied', 'The selected Work permission does not allow this tool.', 'permission_denied')
      }
      systemRoot(dependencies)
      if (request.tool === 'run_command') {
        if (host.runCommand === undefined) throw new NativeFailure('unavailable', 'CMD execution is unavailable.')
        const args = argument as WorkCommandArguments
        let reviewed: WorkCommandArguments
        try { reviewed = parseWorkCommandArguments({ ...args, cwd: args.cwd ?? host.defaultCommandCwd }) }
        catch { throw new NativeFailure('invalid_request', 'The CMD working directory is unavailable.') }
        // Review and execute one detached copy. A mutation during the inline
        // question must not replace the command or its working directory.
        const admitted: WorkToolCall = { ...request, arguments: { ...reviewed }, options: { ...request.options } }
        if (permission === 'ask') {
          if (host.approveCommand === undefined) throw new NativeFailure('unavailable', 'CMD approval is unavailable.')
          const decision = await host.approveCommand(structuredClone(admitted), signal)
          check()
          if (decision === 'timeout') throw new NativeFailure('timeout', 'CMD approval expired. The command was not run.')
          if (decision === 'cancelled') throw new NativeFailure('cancelled', 'CMD approval was cancelled. The command was not run.')
          // Keep app consent distinct from an operating-system denial. The
          // shared permission_denied code must not prompt administrator advice
          // when the user deliberately stopped execution before CMD started.
          if (decision !== 'approved') throw new NativeFailure('permission_denied',
            'The user selected No in the app approval. The CMD command was never started. Windows permissions were not tested; this is a user consent decision, not a Windows or UAC error.')
        }
        check()
        dispatched = true
        return await host.runCommand(admitted, signal)
      }
      if (request.tool === 'list_apps') {
        const available: string[] = []
        for (const id of Object.keys(APPLICATIONS) as ApplicationId[]) {
          try {
            await systemFile(dependencies, APPLICATIONS[id].parts, check)
            available.push(`${id}: ${APPLICATIONS[id].name}`)
          } catch (error) { if (!(error instanceof NativeFailure) || error.code !== 'unavailable') throw error }
          check()
        }
        return result('succeeded', available.length > 0 ? `Available applications:\n${available.join('\n')}` : 'No supported applications are installed in their trusted Windows locations.')
      }
      if (request.tool === 'open_app') {
        const application = APPLICATIONS[argument as ApplicationId]
        let executable = await systemFile(dependencies, application.parts, check)
        await approve(nativeText('Open {application}?', { application: nativeText(application.name) }), nativeText('Work will launch this application without arguments:\n{executable}', { executable }))
        // A dialog can remain open while an application is updated or replaced.
        executable = await systemFile(dependencies, application.parts, check)
        check()
        await new Promise<void>((resolve, reject) => {
          dispatched = true
          const child = dependencies.spawnProcess(executable, [], {
            shell: false, detached: true, stdio: 'ignore', cwd: systemRoot(dependencies),
          })
          child.once('error', () => reject(new NativeFailure('failed', `Windows could not launch ${application.name}.`)))
          child.once('spawn', () => { child.unref(); resolve() })
        })
        check()
        return result('succeeded', `Windows started the ${application.name} launcher. Window visibility is controlled by the application.`)
      }
      const current = await runAudio(dependencies, {}, signal, check, () => {})
      check()
      if (request.tool === 'get_volume') return result('succeeded', `Default output volume: ${current.level}%. ${current.muted ? 'Muted.' : 'Not muted.'}`)
      const level = argument as number
      await approve(nativeText('Set volume to {level}%?', { level }), nativeText('Default multimedia output: {current}% → {level}%. {mute}', { current: current.level, level, mute: nativeText(current.muted ? 'The output will stay muted.' : 'The mute setting will stay unchanged.') }))
      check()
      const updated = await runAudio(dependencies, { level, endpointId: current.endpointId }, signal, check, () => { dispatched = true })
      check()
      if (updated.endpointId !== current.endpointId) throw new NativeFailure('failed', 'The audio helper returned an unexpected output device.')
      return result('succeeded', `Default output volume is now ${updated.level}%. ${updated.muted ? 'The output is muted.' : 'The output is not muted.'}`)
    } catch (error) {
      const failure = error instanceof NativeFailure ? error : new NativeFailure('failed', 'The native Work operation failed.')
      const suffix = dispatched && failure.code !== 'unavailable' ? ' Native dispatch had started; the operation may already have taken effect.' : ''
      return result(failure.code === 'cancelled' ? 'cancelled' : failure.code === 'permission_denied' ? 'denied' : 'failed', failure.message + suffix, failure.code)
    } finally { active = false }
  }
}
