/** Exercise the actual private Electron capture helper against only its own uniquely named synthetic window, never a display or user window. */

import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import ts from 'typescript'

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(import.meta.url)

test('Windows Electron source or explicitly selected ASAR helper captures only the synthetic window and destroys its WebContents',
  // The product's Windows runtime is validated here. Linux capture under Xvfb
  // has not been established; portable owner/frame/permission tests run there.
  { timeout: 30000, skip: process.platform !== 'win32' ? 'Native capture smoke requires the validated Windows desktop runtime.' : false }, async () => {
    const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'elysia-capture-synthetic-test-'))
    try {
      const packaged = process.env.ELYSIA_CAPTURE_PACKAGE_ASAR
      if (packaged !== undefined) {
        // A package test must name the freshly built ASAR explicitly. Falling
        // back to an arbitrary old output would give misleading source-only
        // evidence while never testing the packaged HTML/preload resource paths.
        assert.equal(path.isAbsolute(packaged), true, 'The capture package fixture must be an absolute ASAR path.')
        assert.equal(path.extname(packaged).toLowerCase(), '.asar')
        const details = await fs.lstat(packaged, { bigint: true })
        assert.equal(details.isFile() && !details.isSymbolicLink() && details.nlink === 1n, true)
      }
      await fs.writeFile(path.join(folder, 'package.json'), '{"type":"module"}')
      if (packaged === undefined) {
        for (const name of ['vision-capture-native', 'vision-capture', 'vision-capture-protocol', 'vision-protocol', 'protocol-text']) {
          const code = await fs.readFile(path.join(desktop, 'electron', `${name}.ts`), 'utf8')
          await fs.writeFile(path.join(folder, `${name}.js`), ts.transpileModule(code,
            { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText)
        }
        const preload = path.join(folder, 'vision-capture-preload.cjs')
        await fs.writeFile(preload, ts.transpileModule(await fs.readFile(path.join(desktop, 'electron', 'vision-capture-preload.cts'), 'utf8'),
          { fileName: 'vision-capture-preload.cts', compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText)
        const helperPath = path.join(folder, 'vision-capture.html')
        await fs.copyFile(path.join(desktop, 'public', 'vision-capture.html'), helperPath)
        const helperCode = await fs.readFile(path.join(desktop, 'public', 'vision-capture-frame.js'), 'utf8')
        await fs.writeFile(path.join(folder, 'vision-capture-frame.js'), process.env.ELYSIA_CAPTURE_TEST_TRACE === '1'
          ? helperCode.replace('} catch { result = null }', '} catch(error) { console.log("CAPTURE_TEST:" + error.name + ":" + error.message); result = null }') : helperCode)
      }
      const fixturePath = path.join(folder, 'synthetic.html')
      const title = `Elysia Capture Fixture ${path.basename(folder)}`
      await fs.writeFile(fixturePath, `<!doctype html><!-- Synthetic pixels only; never capture another window. --><html><head><title>${title}</title><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"></head><body style="margin:0;background:rgb(255,0,0)">Synthetic window only</body></html>`)
      const entry = path.join(folder, 'fixture.cjs')
      const adapterLocation = packaged === undefined ? './vision-capture-native.js'
        : pathToFileURL(path.join(packaged, 'dist-electron', 'vision-capture-native.js')).href
      let helperDirectory = folder
      if (packaged === undefined && process.env.ELYSIA_CAPTURE_TEST_SHORT_PATH === '1') {
        // Resolve only this verified temporary fixture's DOS directory alias.
        // The fixed PowerShell command receives its path as data via a scoped
        // environment variable, so no path is interpolated into shell code.
        const resolved = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
          '(New-Object -ComObject Scripting.FileSystemObject).GetFolder($env:ELYSIA_CAPTURE_SHORT_PATH_DIR).ShortPath'],
        { env: { ...process.env, ELYSIA_CAPTURE_SHORT_PATH_DIR: folder }, encoding: 'utf8', windowsHide: true, timeout: 10000 })
        assert.equal(resolved.status, 0, 'Could not obtain the synthetic fixture directory alias.')
        helperDirectory = resolved.stdout.trim()
        assert.match(helperDirectory, /~/u, 'The explicit short-path regression requires a real DOS alias.')
        assert.equal(await fs.realpath(helperDirectory), await fs.realpath(folder))
      }
      const adapterOptions = packaged === undefined ? { helperDocumentPath: path.join(helperDirectory, 'vision-capture.html'),
        preloadPath: path.join(helperDirectory, 'vision-capture-preload.cjs'), timeoutMs: 10000 } : {}
      await fs.writeFile(entry, `/** Verify one-frame capture of this process's own synthetic window. */
const { app, BrowserWindow, nativeImage } = require('electron');
app.setPath('userData', ${JSON.stringify(path.join(folder, 'userData'))});
if (process.env.ELYSIA_CAPTURE_TEST_TRACE === '1') {
  app.on('session-created', ses => {
    for (const name of ['setPermissionCheckHandler', 'setPermissionRequestHandler', 'setDisplayMediaRequestHandler']) {
      const original = ses[name].bind(ses);
      ses[name] = (handler, ...extra) => original(handler === null ? null : (...args) => {
        console.log('CAPTURE_TEST:' + JSON.stringify({ stage: name, permission: typeof args[1] === 'string' ? args[1] : null,
          details: name === 'setPermissionRequestHandler' ? { keys: Object.keys(args[3] || {}), mediaTypes: args[3]?.mediaTypes,
            isMainFrame: args[3]?.isMainFrame } : name === 'setDisplayMediaRequestHandler' ? { origin: args[0].securityOrigin,
            gesture: args[0].userGesture, video: args[0].videoRequested, audio: args[0].audioRequested } : null }));
        return handler(...args);
      }, ...extra);
    }
  });
  app.on('browser-window-created', (_, win) => {
    win.webContents.on('console-message', event => { if (event.message.startsWith('CAPTURE_TEST:')) console.log(event.message); });
    win.webContents.on('preload-error', () => console.log('CAPTURE_TEST:preload-error'));
  });
}
app.whenReady().then(async () => {
  let fixture;
  let successful = false;
  try {
    fixture = new BrowserWindow({ show: false, width: 400, height: 400, title: ${JSON.stringify(title)},
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
    await fixture.loadFile(${JSON.stringify(fixturePath)});
    fixture.showInactive();
    const { createVisionCaptureNative } = await import(${JSON.stringify(adapterLocation)});
    const native = createVisionCaptureNative(${JSON.stringify(adapterOptions)});
    const source = (await native.listSources()).find(item => item.kind === 'window' && item.label === ${JSON.stringify(title)});
    if (!source) throw new Error('Synthetic fixture is unavailable; no other source may be captured.');
    const captured = await native.captureFrame(source, null, new AbortController().signal, () => true);
    const decoded = nativeImage.createFromBuffer(captured.pngBytes);
    const size = decoded.getSize();
    const bitmap = decoded.toBitmap();
    const center = (Math.floor(size.height / 2) * size.width + Math.floor(size.width / 2)) * 4;
    // Native video color-space conversion can round a zero channel to one.
    if (size.width < 200 || size.height < 200 || bitmap[center] > 5 || bitmap[center + 1] > 5 || bitmap[center + 2] < 250)
      throw new Error('Synthetic window pixels were not preserved:' + JSON.stringify({ size, center: [...bitmap.subarray(center, center + 4)] }));
    if (BrowserWindow.getAllWindows().length !== 1) throw new Error('The native capture helper did not drain.');
    console.log('VISION_CAPTURE_SYNTHETIC_OK');
    successful = true;
  } catch (error) { console.error('VISION_CAPTURE_SYNTHETIC_FAILED:' + (error.code || error.message)); }
  finally { fixture?.destroy(); app.exit(successful ? 0 : 1); }
}).catch(() => app.exit(1));
`)
      const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE
      const args = [entry]
      const result = await new Promise((resolve, reject) => {
        const child = spawn(require('electron'), args, { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
        let stdout = '', stderr = ''
        const timer = setTimeout(() => child.kill(), 20000)
        child.stdout.on('data', bytes => { stdout += bytes.toString('utf8'); if (stdout.length > 16384) child.kill() })
        child.stderr.on('data', bytes => { stderr += bytes.toString('utf8'); if (stderr.length > 16384) child.kill() })
        child.once('error', error => { clearTimeout(timer); reject(error) })
        child.once('close', code => { clearTimeout(timer); resolve({ code, stdout, stderr }) })
      })
      assert.equal(result.code, 0, result.stderr + result.stdout)
      assert.match(result.stdout, /VISION_CAPTURE_SYNTHETIC_OK/u)
    } finally {
      assert.equal(path.dirname(folder), path.resolve(os.tmpdir()))
      assert.equal(path.basename(folder).startsWith('elysia-capture-synthetic-test-'), true)
      await fs.rm(folder, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
    }
  })
