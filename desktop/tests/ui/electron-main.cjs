/** Launch the packaged-style Electron test harness used by Playwright. */

const path = require('node:path')
const { pathToFileURL } = require('node:url')

const { app, BrowserWindow, ipcMain } = require('electron')

const rendererPath = path.resolve(__dirname, '..', '..', 'dist', 'index.html')
const rendererUrl = pathToFileURL(rendererPath).href
const preloadPath = path.resolve(__dirname, 'mock-preload.cjs')

let mainWindow = null
let voiceCallWindowManager = null

/** Resolve the replacement main document used by refresh-recovery UI tests. */
function currentMainWindow() {
  return mainWindow !== null && !mainWindow.isDestroyed()
    ? mainWindow
    : BrowserWindow.getAllWindows().find((window) => (
        window.webContents.getURL() === rendererUrl
      )) ?? null
}

/** Start the main mock renderer and attach the production Voice window owner. */
async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 320,
    minHeight: 320,
    title: 'Elysia UI Test',
    backgroundColor: '#171318',
    show: true,
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // The main renderer owns audio even while only the separate call is visible.
      backgroundThrottling: false,
      partition: `elysia-ui-test-${process.pid}`,
    },
  })

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  mainWindow.webContents.on('will-navigate', (event, targetUrl) => {
    if (targetUrl !== rendererUrl) {
      event.preventDefault()
    }
  })
  mainWindow.webContents.session.setPermissionRequestHandler(
    (_webContents, _permission, callback) => {
      callback(false)
    },
  )
  const { VoiceCallWindowManager } = await import(pathToFileURL(
    path.resolve(__dirname, '..', '..', 'dist-electron', 'voice-call-window.js'),
  ).href)
  voiceCallWindowManager = new VoiceCallWindowManager({
    policy: {
      appPath: path.resolve(__dirname, '..', '..'),
      isPackaged: true,
      platform: process.platform,
      developmentUrl: undefined,
    },
    preloadPath: path.resolve(
      __dirname, '..', '..', 'dist-electron', 'voice-call-preload.cjs',
    ),
    currentMainWindow,
    onAction: (action) => {
      const owner = currentMainWindow()
      if (owner !== null && !owner.webContents.isDestroyed()) {
        owner.webContents.send('voice-call:action', action)
      }
    },
  })
  voiceCallWindowManager.registerIpcHandlers(ipcMain, (event) => {
    const owner = currentMainWindow()
    if (owner === null || event.sender !== owner.webContents) {
      throw new Error('Only the main test renderer owns Voice Call state.')
    }
  })
  mainWindow.webContents.on(
    'did-start-navigation',
    (_event, _url, isInPlace, isMainFrame) => {
      if (isMainFrame && !isInPlace) {
        voiceCallWindowManager?.dispose()
      }
    },
  )
  mainWindow.webContents.on('render-process-gone', () => {
    voiceCallWindowManager?.dispose()
  })
  mainWindow.on('closed', () => {
    voiceCallWindowManager?.dispose()
    mainWindow = null
  })

  await mainWindow.loadFile(rendererPath)
}

void app.whenReady()
  .then(createWindow)
  .catch((error) => {
    console.error('Could not start the Elysia UI test window.', error)
    app.exit(1)
  })

app.on('window-all-closed', () => {
  app.quit()
})
