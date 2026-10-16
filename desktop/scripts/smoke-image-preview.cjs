/**
 * Paint a real generated PNG through the production preview reader, owner,
 * custom protocol handler, and renderer CSP in an isolated Electron window.
 * This operator smoke performs no model inference and imports no full app Main.
 */

const assert = require('node:assert/strict')
const { app, BrowserWindow, nativeImage, net, protocol } = require('electron')
const { createHash, randomBytes } = require('node:crypto')
const { mkdirSync, readFileSync } = require('node:fs')
const { lstat, readFile, writeFile } = require('node:fs/promises')
const path = require('node:path')
const { PassThrough } = require('node:stream')
const vm = require('node:vm')

app.disableHardwareAcceleration()
const root = path.resolve(__dirname, '../..')
const flags = new Map()
const arguments_ = process.argv.slice(2)
if (arguments_.length === 1 && arguments_[0] === '--help') {
  process.stdout.write('Usage: electron scripts/smoke-image-preview.cjs --png <absolute-PNG> --output <new-absolute-proof-directory> [--source-report <absolute-JSON>]\nNo models or GPU inference are started.\n')
  process.exit(0)
}
for (let index = 0; index < arguments_.length; index += 2) {
  const name = arguments_[index], value = arguments_[index + 1]
  if (!['--png', '--output', '--source-report'].includes(name) || !value || flags.has(name)) {
    throw new Error('Use --png <verified-export.png> --output <new-proof-directory> [--source-report <report.json>].')
  }
  flags.set(name, value)
}
if (!flags.has('--png') || !flags.has('--output')) throw new Error('A PNG and an isolated proof directory are required.')
for (const value of flags.values()) assert.ok(path.isAbsolute(value), 'Acceptance paths must be absolute.')
const output = flags.get('--output')
mkdirSync(path.dirname(output), { recursive: true })
mkdirSync(output)
mkdirSync(path.join(output, 'electron-user-data'))
// A dedicated userData root avoids opening the actual desktop's preferences,
// sessions, Gallery, and single-instance lifecycle during this native check.
app.setPath('userData', path.join(output, 'electron-user-data'))
app.commandLine.appendSwitch('disable-background-networking')
app.commandLine.appendSwitch('disable-component-update')

const hash = bytes => createHash('sha256').update(bytes).digest('hex')

async function cspFromHtml(file) {
  const { parse } = await import('parse5')
  const bytes = await readFile(file)
  const pending = [parse(bytes.toString('utf8'))]
  while (pending.length) {
    const node = pending.pop()
    const attributes = Object.fromEntries((node.attrs ?? []).map(item => [item.name, item.value]))
    if (node.tagName === 'meta' && attributes['http-equiv']?.toLowerCase() === 'content-security-policy') {
      return { csp: attributes.content, sha256: hash(bytes) }
    }
    pending.push(...(node.childNodes ?? []))
  }
  throw new Error('The production renderer CSP is missing.')
}

function productionProtocol() {
  // Importing Main would start its full desktop lifecycle and real userData.
  // Execute only the two exact local source expressions, retaining their
  // digests in the proof so the native behavior is tied to production code.
  const ts = require('typescript')
  const source = readFileSync(path.join(root, 'desktop/electron/main.ts'), 'utf8')
  const tree = ts.createSourceFile('main.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  let privileges, handler
  function visit(node) {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && node.expression.expression.getText(tree) === 'protocol') {
      if (node.expression.name.text === 'registerSchemesAsPrivileged') privileges = node.arguments[0].getText(tree)
      if (node.expression.name.text === 'handle' && ts.isStringLiteral(node.arguments[0])
        && node.arguments[0].text === 'elysia-image') handler = node.arguments[1].getText(tree)
    }
    ts.forEachChild(node, visit)
  }
  visit(tree)
  assert.ok(privileges && handler, 'Use the actual production scheme declaration and response callback.')
  return { privileges, handler, sha256: hash(Buffer.from(source)), handlerSha256: hash(Buffer.from(handler)) }
}

const production = productionProtocol()
protocol.registerSchemesAsPrivileged(vm.runInNewContext(production.privileges))
let window = null
let currentOwner = null
let reader = null
let pipe = null
let nonce = null
let counter = 0n
const failures = []
const requests = []
const started = Date.now()
const deadline = setTimeout(() => { process.stderr.write('Native preview acceptance exceeded its absolute deadline.\n'); app.exit(1) }, 60_000)

async function main() {
  const sourceMetadata = await lstat(flags.get('--png'))
  assert.ok(sourceMetadata.isFile() && !sourceMetadata.isSymbolicLink() && sourceMetadata.size <= 32 * 1024 * 1024)
  const png = await readFile(flags.get('--png'))
  assert.equal(png.toString('ascii', 12, 16), 'IHDR')
  const width = png.readUInt32BE(16), height = png.readUInt32BE(20), sha256 = hash(png)
  const decoded = nativeImage.createFromBuffer(png)
  assert.deepEqual(decoded.getSize(), { width, height })
  let sourceReportVerified = false
  let sourceReportDimensionsPresent = false
  if (flags.has('--source-report')) {
    const report = JSON.parse(await readFile(flags.get('--source-report'), 'utf8'))
    assert.equal(report.passed, true)
    assert.equal(report.artifact.sha256, sha256)
    assert.equal(report.artifact.byteSize, png.length)
    if (report.artifact.width !== undefined || report.artifact.height !== undefined) {
      assert.deepEqual([report.artifact.width, report.artifact.height], [width, height])
      sourceReportDimensionsPresent = true
    }
    sourceReportVerified = true
  }
  const csp = await cspFromHtml(path.join(root, 'desktop/index.html'))
  const builtCsp = await cspFromHtml(path.join(root, 'desktop/dist/index.html'))
  assert.equal(builtCsp.csp, csp.csp)
  assert.ok(csp.csp.includes('elysia-image:'))
  await app.whenReady()
  const callback = vm.runInNewContext(`(${production.handler})`, {
    Response, Uint8Array,
    backendProcess: { readImagePreviewUrl: url => currentOwner?.readUrl(url) ?? null },
  })
  protocol.handle('elysia-image', request => {
    const response = callback(request)
    requests.push({ method: request.method, status: response.status,
      headers: Object.fromEntries(response.headers.entries()) })
    return response
  })
  const { ImagePreviewChannelReader } = await import('../dist-electron/image-preview-channel.js')
  const { ImagePreviewOwner } = await import('../dist-electron/image-preview-owner.js')
  function replaceOwner(previewTtlMs = 60_000) {
    reader?.dispose()
    currentOwner?.close()
    currentOwner = new ImagePreviewOwner({ previewTtlMs, onFailure: reason => failures.push(reason) })
    nonce = randomBytes(24)
    counter = 0n
    pipe = new PassThrough()
    reader = new ImagePreviewChannelReader(pipe, frame => {
      currentOwner.accept(frame)
      reader.release(frame.previewToken)
    }, reason => failures.push(reason))
  }
  async function preview() {
    counter += 1n
    const token = Buffer.alloc(32)
    nonce.copy(token)
    token.writeBigUInt64BE(counter, 24)
    const metadata = { kind: 'image.gallery.open', artifactId: 'gallery_0123456789abcdef0123456789abcdef', previewToken: token.toString('hex'),
      byteLength: png.length, width, height, sha256, mediaType: 'image/png' }
    const pending = currentOwner.expect(metadata.artifactId, metadata)
    const header = Buffer.alloc(88)
    header.write('ELYSIMG1', 0, 'ascii'); header[8] = 1; token.copy(header, 12)
    header.writeUInt32LE(png.length, 44); header.writeUInt32LE(width, 48); header.writeUInt32LE(height, 52)
    Buffer.from(sha256, 'hex').copy(header, 56)
    const frame = Buffer.concat([header, png])
    for (let offset = 0; offset < frame.length; offset += 16381) pipe.write(frame.subarray(offset, offset + 16381))
    return pending
  }
  const page = path.join(output, 'preview.html')
  const safeCsp = csp.csp.replaceAll('&', '&amp;').replaceAll('"', '&quot;')
  await writeFile(page, `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${safeCsp}"><title>Elysia Image Preview Acceptance</title><style>body{margin:0;background:#242125;color:#f0e9ed;font:18px system-ui;text-align:center}h1{font-size:22px;margin:20px}img{display:block;max-width:92vw;max-height:78vh;margin:0 auto;object-fit:contain}p{color:#c9bcc4;font-size:14px}</style></head><body><h1>Elysia Image Preview</h1><img id="preview" alt="Verified generated image"><p>Actual generated PNG · private preview capability · production CSP</p></body></html>`, { flag: 'wx' })
  window = new BrowserWindow({ width: 960, height: 900, show: false,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } })
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', event => event.preventDefault())
  await window.loadFile(page)
  const isolation = await window.webContents.executeJavaScript('({node:typeof require,process:typeof process,csp:document.querySelector("meta[http-equiv]").content})')
  assert.deepEqual(isolation, { node: 'undefined', process: 'undefined', csp: csp.csp })
  async function loadImage(url) {
    return window.webContents.executeJavaScript(`new Promise(resolve=>{const image=document.getElementById('preview');image.onload=()=>resolve({loaded:true,width:image.naturalWidth,height:image.naturalHeight});image.onerror=()=>resolve({loaded:false});image.src=${JSON.stringify(url)}})`)
  }
  replaceOwner()
  const first = await preview()
  assert.deepEqual(await loadImage(first.url), { loaded: true, width, height })
  assert.equal(requests.at(-1).status, 200)
  assert.equal(requests.at(-1).headers['cache-control'], 'no-store')
  assert.equal(requests.at(-1).headers['x-content-type-options'], 'nosniff')
  assert.equal(requests.at(-1).headers['content-security-policy'], "default-src 'none'")
  await new Promise(resolve => setTimeout(resolve, 100))
  const screenshot = await window.webContents.capturePage()
  assert.ok(!screenshot.isEmpty())
  const screenshotBytes = screenshot.toPNG()
  await writeFile(path.join(output, 'preview.png'), screenshotBytes, { flag: 'wx' })
  assert.equal((await net.fetch(first.url)).status, 404)
  await window.webContents.session.clearCache()
  const reloaded = new Promise(resolve => window.webContents.once('did-finish-load', resolve))
  window.reload()
  await reloaded
  assert.deepEqual(await loadImage(first.url), { loaded: false })
  assert.equal(requests.at(-1).status, 404)
  const revoked = await preview()
  currentOwner.revokeAll()
  assert.deepEqual(await loadImage(revoked.url), { loaded: false })
  assert.equal(requests.at(-1).status, 404)
  replaceOwner(75)
  const expired = await preview()
  await new Promise(resolve => setTimeout(resolve, 125))
  assert.deepEqual(await loadImage(expired.url), { loaded: false })
  assert.equal(requests.at(-1).status, 404)
  const allowed = await preview()
  assert.equal((await net.fetch(allowed.url, { method: 'POST' })).status, 405)
  const receipt = await net.fetch(allowed.url)
  assert.equal(receipt.status, 200)
  assert.equal(hash(Buffer.from(await receipt.arrayBuffer())), sha256)
  assert.equal((await net.fetch(allowed.url)).status, 404)
  assert.deepEqual(failures, [])
  const report = { passed: true, scope: 'isolated Electron renderer, production protocol/CSP/reader/owner; no full app or model inference',
    elapsedSeconds: (Date.now() - started) / 1000, versions: process.versions,
    sourceReportVerified, sourceReportDimensionsPresent, image: { sha256, width, height, byteSize: png.length },
    production: { mainSource: 'desktop/electron/main.ts', mainSha256: production.sha256,
      protocolHandlerSha256: production.handlerSha256, cspSource: 'desktop/index.html', cspSha256: csp.sha256,
      readerSha256: hash(await readFile(path.join(root, 'desktop/dist-electron/image-preview-channel.js'))),
      ownerSha256: hash(await readFile(path.join(root, 'desktop/dist-electron/image-preview-owner.js'))),
      harnessSha256: hash(await readFile(__filename)), csp: csp.csp, builtRendererCspMatches: true },
    screenshot: { sha256: hash(screenshotBytes), ...screenshot.getSize(), byteSize: screenshotBytes.length },
    contextIsolation: true, sandbox: true, nodeIntegration: false, hardwareAccelerationDisabled: true,
    checks: ['fragmented-ELYSIMG1-frame', 'native-PNG-decode', 'real-img-custom-protocol-paint',
      'single-use-404', 'reload-404-after-cache-clear', 'revocation-404', 'expiry-404', 'POST-405-preserves-capability',
      'native-response-digest', 'production-CSP', 'renderer-no-Node', 'window-only-screenshot'], requests }
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' })
  process.stdout.write(JSON.stringify({ passed: true, report: path.join(output, 'report.json'), screenshot: path.join(output, 'preview.png') }) + '\n')
}

void main().then(() => {
  reader?.dispose(); currentOwner?.close(); window?.destroy(); clearTimeout(deadline); app.exit(0)
}, error => {
  reader?.dispose(); currentOwner?.close(); window?.destroy(); clearTimeout(deadline)
  process.stderr.write(`Native preview acceptance failed: ${String(error?.stack ?? error)}\n`)
  app.exit(1)
})
