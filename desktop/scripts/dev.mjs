/**
 * @fileoverview Compile Electron, start Vite, and run the complete desktop app.
 *
 * The renderer must be listening before Electron loads it, and both processes
 * must share one shutdown owner so Ctrl+C cannot leave a hidden development
 * server or orphaned desktop process behind.
 */

import { spawn } from 'node:child_process'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

import { build, createServer } from 'vite'

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const require = createRequire(import.meta.url)
const electronExecutable = require('electron')
const typeScriptCompiler = path.join(
  desktopRoot,
  'node_modules',
  'typescript',
  'bin',
  'tsc',
)

let activeChild = null
let developmentServer = null
let terminationSignal = null

function waitForChild(child) {
  return new Promise((resolve, reject) => {
    child.once('error', reject)
    child.once('exit', (code, signal) => resolve({ code, signal }))
  })
}

async function stopOwnedProcesses() {
  const child = activeChild
  activeChild = null
  if (child !== null && child.exitCode === null && child.signalCode === null) {
    child.kill()
  }
  const server = developmentServer
  developmentServer = null
  if (server !== null) {
    await server.close()
  }
}

async function runDesktopDevelopment() {
  const compiler = spawn(
    process.execPath,
    [typeScriptCompiler, '--project', 'tsconfig.electron.json'],
    { cwd: desktopRoot, stdio: 'inherit' },
  )
  activeChild = compiler
  const compilation = await waitForChild(compiler)
  activeChild = null
  if (terminationSignal !== null) {
    return
  }
  if (compilation.code !== 0) {
    throw new Error('Electron TypeScript compilation failed.')
  }

  await build({
    configFile: path.join(desktopRoot, 'vite.preload.config.ts'),
    root: desktopRoot,
  })
  if (terminationSignal !== null) {
    return
  }

  const server = await createServer({
    configFile: path.join(desktopRoot, 'vite.config.ts'),
    root: desktopRoot,
    server: {
      host: 'localhost',
      port: 5173,
      strictPort: true,
    },
  })
  developmentServer = server
  if (terminationSignal !== null) {
    await stopOwnedProcesses()
    return
  }
  await server.listen()
  if (terminationSignal !== null) {
    await stopOwnedProcesses()
    return
  }
  server.printUrls()

  const electron = spawn(electronExecutable, ['.'], {
    cwd: desktopRoot,
    env: process.env,
    stdio: 'inherit',
  })
  activeChild = electron
  if (terminationSignal !== null) {
    await stopOwnedProcesses()
    return
  }
  const result = await waitForChild(electron)
  activeChild = null
  await stopOwnedProcesses()
  if (terminationSignal !== null) {
    return
  }
  if (result.code !== 0) {
    throw new Error(
      result.signal === null
        ? `Electron exited with code ${String(result.code)}.`
        : `Electron exited after signal ${result.signal}.`,
    )
  }
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => {
    terminationSignal = signal
    void stopOwnedProcesses().finally(() => {
      process.exitCode = signal === 'SIGINT' ? 130 : 143
    })
  })
}

void runDesktopDevelopment().catch(async (error) => {
  await stopOwnedProcesses()
  if (terminationSignal !== null) {
    return
  }
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
