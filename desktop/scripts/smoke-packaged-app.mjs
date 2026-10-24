/** @fileoverview Exercise packaged startup recovery and native window chrome with an isolated profile. */

import assert from 'node:assert/strict'
import { mkdtemp, mkdir, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { _electron as electron, expect } from '@playwright/test'

const desktopRoot = fileURLToPath(new URL('..', import.meta.url))
const executablePath = path.resolve(process.argv[2] ?? path.join(desktopRoot, 'out/win-unpacked/Elysia.exe'))
const temporary = await mkdtemp(path.join(os.tmpdir(), 'elysia-exe-smoke-'))
const profile = path.join(temporary, 'profile')
await mkdir(profile)
const env = { ...process.env }
for (const key of Object.keys(env)) {
  if (/^(ELYSIA_|PYTHON|OLLAMA_|MODEL_NAME|TRANSCRIPTION_)/u.test(key)) delete env[key]
}
Object.assign(env, {
  PATH: path.join(env.SystemRoot ?? 'C:/Windows', 'System32'),
  APPDATA: temporary, LOCALAPPDATA: temporary,
  OLLAMA_HOST: 'http://127.0.0.1:1', MODEL_NAME: 'smoke-local:latest',
  ELYSIA_PYTHON: path.join(temporary, 'missing-python.exe'),
  ELYSIA_PROJECT_ROOT: path.join(temporary, 'missing-source'),
})
let application
try {
  // Playwright defaults to emulating light media, which can disguise a mismatch
  // between the renderer and native caption. Exercise the real system theme.
  application = await electron.launch({ executablePath, args: [`--user-data-dir=${profile}`], env, colorScheme: null, timeout: 60_000 })
  const identity = await application.evaluate(({ app }) => ({ packaged: app.isPackaged, userData: app.getPath('userData') }))
  assert.equal(identity.packaged, true)
  assert.equal(path.resolve(identity.userData), path.resolve(profile), 'Smoke must own an isolated profile.')
  const window = await application.firstWindow({ timeout: 60_000 })
  // Poll IPC from the driver: waitForFunction treats a returned Promise as a
  // truthy object in some Electron/Playwright versions and can race startup.
  const deadline = Date.now() + 60_000
  let settled = false
  let startupProfile
  while (Date.now() < deadline) {
    const snapshot = await window.evaluate(() => window.elysiaDesktop?.getSnapshot())
    if (!startupProfile && snapshot?.capabilities.includes('settings.management')
        && ['initializing', 'error'].includes(snapshot.status)) {
      // Settings can open before model initialization finishes. Keep this
      // request in flight while initialization fails to catch lost request IDs
      // that would otherwise disconnect an authenticated, repairable Backend.
      startupProfile = window.evaluate(() => window.elysiaDesktop.getProfile())
        .then(value => ({ value }), error => ({ error: String(error) }))
    }
    if (snapshot?.status === 'error' && snapshot.capabilities.includes('settings.management')) {
      settled = true
      break
    }
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  assert.ok(settled, 'Installed backend must preserve settings when Ollama is absent.')
  assert.ok(startupProfile, 'Startup must expose the user profile without a model.')
  const loadedProfile = await startupProfile
  assert.ok(!loadedProfile.error, JSON.stringify(loadedProfile))
  assert.equal(loadedProfile.value.kind, 'profile')
  await window.getByText('ollama pull smoke-local:latest', { exact: true }).waitFor({ timeout: 10_000 })
  const state = await window.evaluate(async () => ({
    snapshot: await window.elysiaDesktop.getSnapshot(),
    settings: await window.elysiaDesktop.getSettings().catch(error => ({ error: error.message })),
    body: document.body.innerText,
  }))
  assert.ok(!state.settings.error, JSON.stringify(state))
  assert.equal(state.settings.settings.ollamaHost, 'http://127.0.0.1:1')
  assert.ok(state.body.includes('Elysia'))
  assert.match(state.snapshot.error, /Ollama|model/iu)
  assert.equal(state.snapshot.initializationErrorCode, 'backend.ollama_unavailable')
  if (process.platform === 'win32') {
    for (const zoom of [1, 1.25, 1.5]) {
      await application.evaluate(({ BrowserWindow }, value) => {
        BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(value)
      }, zoom)
      await window.waitForFunction(value => {
        const titlebar = document.querySelector('.desktop-titlebar')
        const shell = document.querySelector('.app-shell')
        if (!titlebar || !shell) return false
        const rect = titlebar.getBoundingClientRect()
        return Math.abs(rect.height * value - 32) < 2
          && Math.abs(shell.getBoundingClientRect().y - rect.bottom) < 1
      }, zoom)
      assert.equal(await window.locator('.desktop-titlebar').innerText(), '')
    }
    await application.evaluate(({ BrowserWindow }) => {
      const main = BrowserWindow.getAllWindows()[0]
      main.webContents.setZoomFactor(1)
      main.maximize()
    })
    await expect.poll(() => application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isMaximized())).toBe(true)
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].unmaximize())
    await expect.poll(() => application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isMaximized())).toBe(false)
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].minimize())
    await expect.poll(() => application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isMinimized())).toBe(true)
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].restore())
    await expect.poll(() => application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isMinimized())).toBe(false)
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setFullScreen(true))
    await window.waitForFunction(() => document.querySelector('.app-shell')?.getBoundingClientRect().y === 0)
    // Reload can happen inside fullscreen. The ready handshake must restore the
    // native state instead of waiting for a future enter/leave event.
    await window.reload()
    await window.waitForFunction(() => document.querySelector('.app-shell')?.getBoundingClientRect().y === 0)
    await application.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setFullScreen(false))
    await window.waitForFunction(() => (document.querySelector('.desktop-titlebar')?.getBoundingClientRect().height ?? 0) > 0)
    for (const theme of ['dark', 'light']) {
      await window.evaluate(value => window.elysiaDesktop.setThemePreference(value), theme)
      assert.equal(await application.evaluate(({ nativeTheme }) => nativeTheme.shouldUseDarkColors), theme === 'dark')
      await window.waitForFunction(value => document.documentElement.dataset.theme === value, theme)
    }
  }
  await window.screenshot({ path: path.join(desktopRoot, 'out/packaged-first-launch.png'), fullPage: true })
  console.log('Packaged EXE passed: real Main/preload/renderer, isolated profile, bundled Python, model setup, Settings IPC, caption scaling, minimize/maximize/restore, fullscreen reload and native themes.')
} finally {
  await application?.close()
  await rm(temporary, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}
