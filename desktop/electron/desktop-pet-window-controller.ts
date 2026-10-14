/**
 * Place one manager-owned Bongo Cat window after the reviewed program loads.
 *
 * A prewarmed helper parks the initially hidden SFML window offscreen before
 * the program shows its splash. A read-only flag in the exact pinned build
 * establishes loading completion; no injected code, process-memory writes, or
 * third-party configuration edits are used. Placement is one-shot, preserving
 * the pet's native drag/resize controls after startup.
 */

import { spawn, type ChildProcess, type SpawnOptions } from 'node:child_process'
import path from 'node:path'
import type { DesktopPetProgramDescriptor, DesktopPetWindowPreparation } from './desktop-pet-program-manager.js'

/** A physical-pixel rectangle, including negative coordinates on other monitors. */
export interface DesktopPetNativeRectangle {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

/** Main-owned app/display measurements; scaleFactor converts DIP size limits. */
export interface DesktopPetWindowPlacement {
  readonly appBounds: DesktopPetNativeRectangle
  readonly workArea: DesktopPetNativeRectangle
  readonly scaleFactor: number
}

/** Replace the native subprocess in deterministic ownership and protocol tests. */
export interface DesktopPetWindowControllerDependencies {
  readonly platform: NodeJS.Platform
  /** Start only the fixed Windows helper with closed arguments and piped input. */
  readonly spawnController: (
    executable: string, args: readonly string[], options: SpawnOptions,
  ) => ChildProcess
}

const FAILURE = 'Desktop Pet window could not be prepared.'
const HELPER_TIMEOUT_MS = 14_000

function validRectangle(value: DesktopPetNativeRectangle): boolean {
  return [value.x, value.y, value.width, value.height].every(Number.isSafeInteger)
    && Math.abs(value.x) <= 1_000_000 && Math.abs(value.y) <= 1_000_000
    && value.width > 0 && value.height > 0
    && value.width <= 32_768 && value.height <= 32_768
}

/** Fit the pet's existing aspect ratio beside the app, clamped to its work area. */
export function getDesktopPetInitialBounds(
  placement: DesktopPetWindowPlacement,
  sourceWidth: number,
  sourceHeight: number,
): DesktopPetNativeRectangle {
  const { appBounds, workArea, scaleFactor } = placement
  if (!validRectangle(appBounds) || !validRectangle(workArea)
    || !Number.isFinite(scaleFactor) || scaleFactor < .5 || scaleFactor > 8
    || !Number.isSafeInteger(sourceWidth) || !Number.isSafeInteger(sourceHeight)
    || sourceWidth <= 0 || sourceHeight <= 0 || sourceWidth > 32_768 || sourceHeight > 32_768) {
    throw new Error(FAILURE)
  }
  const inset = Math.round(12 * scaleFactor)
  const availableWidth = Math.max(1, workArea.width - inset * 2)
  const availableHeight = Math.max(1, workArea.height - inset * 2)
  const maxHeight = Math.min(availableHeight,
    Math.max(260 * scaleFactor, Math.min(380 * scaleFactor, appBounds.height * .4)))
  const maxWidth = Math.min(availableWidth, Math.max(180 * scaleFactor, appBounds.width * .25))
  const scale = Math.min(maxWidth / sourceWidth, maxHeight / sourceHeight)
  const width = Math.max(1, Math.round(sourceWidth * scale))
  const height = Math.max(1, Math.round(sourceHeight * scale))
  const right = Math.min(workArea.x + workArea.width - inset,
    appBounds.x + appBounds.width + width * .75)
  const bottom = Math.min(workArea.y + workArea.height - inset,
    appBounds.y + appBounds.height - Math.round(8 * scaleFactor))
  return Object.freeze({
    x: Math.round(Math.max(workArea.x + inset, right - width)),
    y: Math.round(Math.max(workArea.y + inset, bottom - height)), width, height,
  })
}

// A fixed script accepts only a JSON PID/image pair and numeric placement over
// stdin. Holding a process handle and rechecking it before every HWND operation
// prevents a retired child's recycled PID from selecting a manually run copy.
// Thread DPI awareness makes the Main-supplied physical coordinates exact.
const WINDOWS_HELPER = String.raw`
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
public static class ElysiaPetWindow {
  delegate bool EnumCallback(IntPtr hwnd, IntPtr data);
  [StructLayout(LayoutKind.Sequential)] struct Rect { public int L,T,R,B; }
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumCallback callback, IntPtr data);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr hwnd, StringBuilder text, int length);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
  [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr hwnd, out Rect rect);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hwnd, int command);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int w, int h, uint flags);
  [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
  [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);
  [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref int size);
  [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr process, IntPtr address, byte[] buffer, IntPtr size, out IntPtr read);
  static int ReadGuard(IntPtr process, IntPtr imageBase) {
    var bytes=new byte[4]; IntPtr read;
    if(!ReadProcessMemory(process,new IntPtr(imageBase.ToInt64()+0xECA10),bytes,new IntPtr(4),out read)||read.ToInt64()!=4) throw new Exception();
    return BitConverter.ToInt32(bytes,0);
  }
  static long ReadPresentedClock(IntPtr process, IntPtr imageBase) {
    var bytes=new byte[8]; IntPtr read;
    if(!ReadProcessMemory(process,new IntPtr(imageBase.ToInt64()+0xEC6F0),bytes,new IntPtr(8),out read)||read.ToInt64()!=8) throw new Exception();
    return BitConverter.ToInt64(bytes,0);
  }
  static long ReadLoopClock(IntPtr process, IntPtr imageBase) {
    var bytes=new byte[8]; IntPtr read;
    if(!ReadProcessMemory(process,new IntPtr(imageBase.ToInt64()+0xECA08),bytes,new IntPtr(8),out read)||read.ToInt64()!=8) throw new Exception();
    return BitConverter.ToInt64(bytes,0);
  }
  static void VerifyImage(string filePath, long size, string expected) {
    using(var file=new FileStream(filePath,FileMode.Open,FileAccess.Read,FileShare.Read)) {
      if(file.Length!=size) throw new Exception();
      using(var sha=SHA256.Create()) {
        string digest=BitConverter.ToString(sha.ComputeHash(file)).Replace("-","");
        if(digest!=expected) throw new Exception();
      }
    }
  }
  static bool Owned(IntPtr process, IntPtr hwnd, uint pid) {
    uint actual; GetWindowThreadProcessId(hwnd, out actual);
    var name = new StringBuilder(128); GetClassName(hwnd,name,name.Capacity);
    return WaitForSingleObject(process,0)==258 && actual==pid && name.ToString()=="SFML_Window";
  }
  public static void Run(uint pid, string executable) {
    if (pid==0 || SetThreadDpiAwarenessContext(new IntPtr(-4))==IntPtr.Zero) throw new Exception();
    IntPtr process=OpenProcess(0x00100000|0x1000|0x0010,false,pid);
    if(process==IntPtr.Zero) throw new Exception();
    try {
      var image=new StringBuilder(32768); int length=image.Capacity;
      if(!QueryFullProcessImageName(process,0,image,ref length) || !String.Equals(image.ToString(),executable,StringComparison.OrdinalIgnoreCase)) throw new Exception();
      var timer=Stopwatch.StartNew(); IntPtr found=IntPtr.Zero;
      while(timer.ElapsedMilliseconds<12500) {
        if(WaitForSingleObject(process,0)!=258) throw new Exception();
        int count=0; IntPtr candidate=IntPtr.Zero;
        EnumWindows((hwnd,data)=> { if(Owned(process,hwnd,pid)) {count++; candidate=hwnd;} return true; },IntPtr.Zero);
        if(count>1) throw new Exception();
        if(count==1) {
          // The initial STARTUPINFO-hidden interval is observed explicitly.
          // An already visible first window cannot prove splash suppression.
          if(IsWindowVisible(candidate)) {ShowWindow(candidate,0); throw new Exception();}
          Rect initial; if(!GetWindowRect(candidate,out initial)) throw new Exception();
          int width=initial.R-initial.L;
          int left=GetSystemMetrics(76), top=GetSystemMetrics(77), screenWidth=GetSystemMetrics(78);
          int parkedX=left-width-128;
          if(parkedX < -32000) parkedX=left+screenWidth+128;
          if(parkedX+width>32000) throw new Exception();
          if(!SetWindowPos(candidate,IntPtr.Zero,parkedX,top,0,0,0x0010|0x0004|0x0001)) throw new Exception();
          found=candidate; break;
        }
        Thread.Sleep(5);
      }
      if(found==IntPtr.Zero || !Owned(process,found,pid)) throw new Exception();
      // This RVA is a version-specific contract, not a generic memory scan.
      // In the reviewed 0.1.6 image the MSVC static clock guard is initialized
      // only after the logo routine returns with model loading complete.
      // 0 is untouched, -1 is initialization in progress, and < -1 is the
      // completed epoch. Rehash the exact image before interpreting its bytes.
      VerifyImage(executable,1523200,"4D416349B00E1C1948082E3C0923FB654A4CF24F648EA3B56AE40FA39360EF8A");
      string directory=Path.GetDirectoryName(executable);
      VerifyImage(Path.Combine(directory,"sfml-window-2.dll"),123392,"97E07E9CE9CEDC36AC8B66A54BD378CFFB808987EFCB827230BD6F321E770FC4");
      VerifyImage(Path.Combine(directory,"sfml-system-2.dll"),50176,"AFB94EF85C8B663D331B5BD14DD0307EA127DC2794E20E532C8AE19942D08AAD");
      IntPtr imageBase;
      using(var native=Process.GetProcessById((int)pid)) {
        if(!String.Equals(native.MainModule.FileName,executable,StringComparison.OrdinalIgnoreCase)) throw new Exception();
        imageBase=native.MainModule.BaseAddress;
      }
      bool loaded=false;
      while(timer.ElapsedMilliseconds<12500) {
        if(!Owned(process,found,pid)) throw new Exception();
        if(ReadGuard(process,imageBase)<-1) {loaded=true; break;}
        Thread.Sleep(5);
      }
      if(!loaded) throw new Exception();
      Rect client; if(!GetClientRect(found,out client)) throw new Exception();
      Console.WriteLine("WINDOW "+(client.R-client.L)+" "+(client.B-client.T)); Console.Out.Flush();
      string line=Console.ReadLine(); if(line==null) throw new Exception();
      string[] values=line.Split(' '); if(values.Length!=4) throw new Exception();
      int x,y,w,h;
      if(!int.TryParse(values[0],out x)||!int.TryParse(values[1],out y)||!int.TryParse(values[2],out w)||!int.TryParse(values[3],out h)
        ||Math.Abs((long)x)>1000000||Math.Abs((long)y)>1000000||w<=0||h<=0||w>32768||h>32768) throw new Exception();
      // Crossing monitors can trigger native WM_DPICHANGED resizing even with
      // SWP_NOSIZE. Hide while still parked, move to the destination DPI, then
      // apply the physical size there before any visible presentation.
      if(!Owned(process,found,pid)) throw new Exception();
      ShowWindow(found,0);
      if(IsWindowVisible(found)||!SetWindowPos(found,IntPtr.Zero,x,y,0,0,0x0010|0x0004|0x0001)
        ||!Owned(process,found,pid)||!SetWindowPos(found,IntPtr.Zero,x,y,w,h,0x0010|0x0004)) throw new Exception();
      // The guard precedes the first normal frame. In the two pinned SFML
      // DLLs display() updates this aligned int64 clock only after SwapBuffers.
      // Resize while hidden, then require TWO later presentation advances: a
      // frame already in flight at WM_SIZE cannot be mistaken for its redraw.
      // Uncapped mode does not advance the SFML frame clock. Its normal-loop
      // clock advances from the real running loop roughly every two seconds;
      // two such changes are the slower semantic fallback, not a show timer.
      long baseline=ReadPresentedClock(process,imageBase);
      long loopBaseline=ReadLoopClock(process,imageBase);
      int presentations=0;
      int loopAdvances=0;
      while(timer.ElapsedMilliseconds<12500) {
        if(!Owned(process,found,pid)) throw new Exception();
        Rect settled; if(!GetWindowRect(found,out settled)) throw new Exception();
        if(IsWindowVisible(found)||settled.L!=x||settled.T!=y||settled.R-settled.L!=w||settled.B-settled.T!=h) {
          // WM_DPICHANGED may finish asynchronously. Correct only while
          // hidden and restart both redraw barriers after every correction.
          ShowWindow(found,0);
          if(IsWindowVisible(found)||!SetWindowPos(found,IntPtr.Zero,x,y,w,h,0x0010|0x0004)) throw new Exception();
          baseline=ReadPresentedClock(process,imageBase);
          loopBaseline=ReadLoopClock(process,imageBase);
          presentations=0; loopAdvances=0;
          Thread.Sleep(5); continue;
        }
        long current=ReadPresentedClock(process,imageBase);
        if(current>baseline) {presentations++; baseline=current; if(presentations==2) break;}
        long loopCurrent=ReadLoopClock(process,imageBase);
        if(loopCurrent>loopBaseline) {loopAdvances++; loopBaseline=loopCurrent; if(loopAdvances==2) break;}
        Thread.Sleep(5);
      }
      if((presentations!=2 && loopAdvances!=2) || !Owned(process,found,pid)) throw new Exception();
      Rect before; if(IsWindowVisible(found)||!GetWindowRect(found,out before)
        ||before.L!=x||before.T!=y||before.R-before.L!=w||before.B-before.T!=h) throw new Exception();
      // Reveal only after semantic loading and redraw completion, without
      // stealing focus. The helper exits, leaving later drag/resize native.
      ShowWindow(found,4);
      Rect actual;
      if(!Owned(process,found,pid)||!IsWindowVisible(found)||!GetWindowRect(found,out actual)
        ||actual.L!=x||actual.T!=y||actual.R-actual.L!=w||actual.B-actual.T!=h) {
        if(Owned(process,found,pid)) ShowWindow(found,0);
        throw new Exception();
      }
      Console.WriteLine("READY"); Console.Out.Flush(); return;
    } finally { CloseHandle(process); }
  }
}
'@
[Console]::WriteLine('ARMED')
[Console]::Out.Flush()
$request = [Console]::ReadLine() | ConvertFrom-Json
[ElysiaPetWindow]::Run([uint32]$request.pid, [string]$request.executable)
exit 0
} catch { exit 1 }
`

/** Create the bounded PID-specific startup hook used by the process manager. */
export function createDesktopPetWindowPreparation(
  getPlacement: () => DesktopPetWindowPlacement | null,
  dependencies: Partial<DesktopPetWindowControllerDependencies> = {},
): DesktopPetWindowPreparation {
  const platform = dependencies.platform ?? process.platform
  const spawnController = dependencies.spawnController ?? spawn
  let reserved: {
    signal: AbortSignal
    armed: Promise<void>
    completed: Promise<void>
    begin: (child: ChildProcess, descriptor: DesktopPetProgramDescriptor) => void
    abort: () => void
  } | null = null
  const warmup = async (signal: AbortSignal): Promise<void> => {
    if (reserved !== null) throw new Error(FAILURE)
    const placement = getPlacement()
    const systemRoot = process.env.SystemRoot ?? process.env.WINDIR
    if (platform !== 'win32' || placement === null || signal.aborted
      || systemRoot === undefined || !path.win32.isAbsolute(systemRoot) || /[\0\r\n]/u.test(systemRoot)) {
      throw new Error(FAILURE)
    }
    // Validate bounds before launching any native helper or touching a window.
    getDesktopPetInitialBounds(placement, 1, 1)
    let resolveArmed!: () => void
    let rejectArmed!: (error: Error) => void
    const armed = new Promise<void>((resolve, reject) => { resolveArmed = resolve; rejectArmed = reject })
    let begin!: (child: ChildProcess, descriptor: DesktopPetProgramDescriptor) => void
    let cancel!: () => void
    const completed = new Promise<void>((resolve, reject) => {
      let helper: ChildProcess
      let settled = false
      let acknowledged = false
      let begun = false
      let placed = false
      let ready = false
      let pending = ''
      let outputBytes = 0
      let timer: ReturnType<typeof setTimeout> | undefined
      const finish = (success: boolean): void => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        signal.removeEventListener('abort', abort)
        if (reserved?.signal === signal) reserved = null
        helper?.stdin?.destroy()
        if (!success) helper?.kill()
        if (success) resolve()
        else { rejectArmed(new Error(FAILURE)); reject(new Error(FAILURE)) }
      }
      const abort = (): void => { finish(false) }
      cancel = abort
      begin = (child, descriptor): void => {
        if (settled || !acknowledged || begun || signal.aborted
          || typeof child.pid !== 'number' || !Number.isSafeInteger(child.pid) || child.pid <= 0
          || child.exitCode !== null || child.signalCode !== null
          || !path.win32.isAbsolute(descriptor.executablePath)) { finish(false); return }
        begun = true
        clearTimeout(timer)
        timer = setTimeout(abort, HELPER_TIMEOUT_MS)
        helper.stdin?.write(`${JSON.stringify({ pid: child.pid, executable: descriptor.executablePath })}\n`)
      }
      try {
        helper = spawnController(path.win32.join(systemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe'),
          ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(WINDOWS_HELPER, 'utf16le').toString('base64')],
          { windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'ignore'],
            env: { SystemRoot: systemRoot, WINDIR: systemRoot, TEMP: process.env.TEMP,
              TMP: process.env.TMP, PATH: path.win32.join(systemRoot, 'System32') } })
        helper.once('error', abort)
        helper.stdin?.on('error', abort)
        helper.stdout?.on('data', (chunk: Buffer) => {
          if (settled) return
          outputBytes += chunk.byteLength
          if (outputBytes > 256) { finish(false); return }
          pending += chunk.toString('utf8')
          let newline: number
          while ((newline = pending.indexOf('\n')) >= 0) {
            const line = pending.slice(0, newline).replace(/\r$/u, '')
            pending = pending.slice(newline + 1)
            const window = /^WINDOW ([1-9][0-9]{0,4}) ([1-9][0-9]{0,4})$/u.exec(line)
            if (!acknowledged && line === 'ARMED') {
              acknowledged = true
              resolveArmed()
            } else if (begun && !placed && window !== null) {
              try {
                const currentPlacement = getPlacement()
                if (currentPlacement === null) throw new Error(FAILURE)
                const bounds = getDesktopPetInitialBounds(currentPlacement, Number(window[1]), Number(window[2]))
                placed = true
                helper.stdin?.write(`${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}\n`)
              } catch { finish(false); return }
            } else if (begun && placed && !ready && line === 'READY') ready = true
            else { finish(false); return }
          }
        })
        helper.once('close', (code) => { finish(code === 0 && ready && pending.length === 0 && !signal.aborted) })
        signal.addEventListener('abort', abort, { once: true })
        timer = setTimeout(abort, HELPER_TIMEOUT_MS)
        if (signal.aborted) { finish(false); return }
      } catch { finish(false) }
    })
    // Completion may reject during prewarming, before a child exists to await
    // it. Retain that failure for the caller without an unhandled rejection.
    void completed.catch(() => {})
    reserved = { signal, armed, completed, begin, abort: cancel }
    try { await armed } catch (error) {
      if (reserved?.signal === signal) reserved = null
      throw error
    }
  }
  const prepare: DesktopPetWindowPreparation = async (child, descriptor, signal): Promise<void> => {
    const session = reserved
    if (session === null || session.signal !== signal) throw new Error(FAILURE)
    session.begin(child, descriptor)
    await session.completed
  }
  prepare.warmup = warmup
  return prepare
}
