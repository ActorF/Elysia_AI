const path = require('node:path')
const { pathToFileURL } = require('node:url')

const { app, BrowserWindow } = require('electron')

const rendererPath = path.resolve(__dirname, '..', '..', 'dist', 'index.html')
const rendererUrl = pathToFileURL(rendererPath).href
const preloadPath = path.resolve(__dirname, 'mock-preload.cjs')

let mainWindow = null

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
  mainWindow.on('closed', () => {
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
