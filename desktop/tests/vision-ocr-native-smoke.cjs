/** Render fixed multilingual text in an isolated canvas and verify real canonical PNG-to-Tesseract recognition without user images or capture APIs. */

const assert = require('node:assert/strict')
const { access } = require('node:fs/promises')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const { app, BrowserWindow, nativeImage } = require('electron')

const directory = process.env.ELYSIA_OCR_FIXTURE_DIRECTORY
assert.ok(directory && path.isAbsolute(directory), 'The isolated acceptance directory is required.')
app.setPath('userData', path.join(directory, 'userData'))

const cases = [
  { language: 'eng', font: 'Arial', fontFile: 'arial.ttf', lines: ['Local OCR 2026', 'English text recognition'] },
  { language: 'chi_sim+eng', font: 'Microsoft YaHei', fontFile: 'msyh.ttc', lines: ['简体中文识别测试', 'Local OCR 2026'] },
  { language: 'chi_tra+eng', font: 'Microsoft JhengHei', fontFile: 'msjh.ttc', lines: ['繁體中文識別測試', 'Local OCR 2026'] },
]

function normalized(value) { return value.replace(/\s/gu, '') }

function verifyBoxes(result, bitmap) {
  assert.ok(result.words.length >= 3, 'Recognition must publish usable word positions.')
  for (const word of result.words) {
    assert.ok(Number.isFinite(word.confidence) && word.confidence >= 0 && word.confidence <= 1)
    assert.ok([word.x, word.y, word.width, word.height].every(Number.isInteger))
    assert.ok(word.x >= 0 && word.y >= 0 && word.width > 0 && word.height > 0)
    assert.ok(word.x + word.width <= result.width && word.y + word.height <= result.height)
    // A merely well-formed box can still use the wrong coordinate space.
    // Require ink in each reported rectangle against the original PNG pixels.
    let ink = 0
    for (let y = word.y; y < word.y + word.height; y += 1) {
      for (let x = word.x; x < word.x + word.width; x += 1) {
        const offset = (y * result.width + x) * 4
        if (bitmap[offset] < 128 && bitmap[offset + 1] < 128 && bitmap[offset + 2] < 128) ink += 1
      }
    }
    assert.ok(ink > word.width * word.height * 0.01, `OCR box for ${JSON.stringify(word.text)} misses its rendered text.`)
  }
  assert.ok(result.words.some((word) => word.y < 120), 'The first rendered text line must have positional evidence.')
  assert.ok(result.words.some((word) => word.y > 120), 'The second rendered text line must have positional evidence.')
}

void app.whenReady().then(async () => {
  let fixture
  try {
    const modules = process.env.ELYSIA_OCR_PACKAGE_ASAR
      ? path.join(process.env.ELYSIA_OCR_PACKAGE_ASAR, 'dist-electron') : path.join(__dirname, '../dist-electron')
    const { TesseractOcrAdapter } = await import(pathToFileURL(path.join(modules, 'vision-ocr.js')).href)
    const { decodeVisionImage, VisionImageController } = await import(pathToFileURL(path.join(modules, 'vision-images.js')).href)
    const { VisionAnalysisController } = await import(pathToFileURL(path.join(modules, 'vision-analysis.js')).href)
    const adapter = new TesseractOcrAdapter()
    const owner = { chatId: 'chat_ocr_native', projectId: null, mode: 'chat', archived: false, ready: true, busy: false }
    const images = new VisionImageController({ getOwner: () => owner,
      chooseFile: async () => { throw new Error('Synthetic OCR acceptance must never open a file picker.') },
      readFile: async () => { throw new Error('Synthetic OCR acceptance must never read a user image.') },
      decode: (bytes) => decodeVisionImage(bytes, nativeImage) })
    const analysis = new VisionAnalysisController({ getOwner: () => owner,
      getImageIdentity: (chatId, imageId) => images.getImageIdentity(chatId, imageId),
      getImage: (chatId, imageId) => images.getImage(chatId, imageId),
      readiness: (signal) => adapter.readiness(signal),
      recognize: (image, language, signal) => adapter.run(image.pngBytes, image.width, image.height, language, signal) })
    // Exercise the same controller and strict public DTO parser used by Main.
    // Adapter-only acceptance would miss a valid engine version or transcript
    // being rejected while crossing the renderer-facing protocol boundary.
    const checked = await analysis.checkOcr(owner.chatId)
    assert.equal(checked.ocr.error, null, 'The real engine readiness must survive the public analysis protocol.')
    const readiness = checked.ocr.readiness
    assert.ok(readiness)
    assert.equal(readiness.status, 'ready', 'Install Tesseract at its default path or set ELYSIA_TESSERACT_PATH before this test.')
    assert.deepEqual(readiness.languages, ['eng', 'chi_sim', 'chi_tra'], 'English and both Chinese language data files are required.')

    fixture = new BrowserWindow({ show: false, width: 1200, height: 280,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, partition: `ocr-synthetic-${process.pid}` } })
    fixture.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
    fixture.webContents.session.setPermissionCheckHandler(() => false)
    fixture.webContents.session.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] },
      (_details, callback) => callback({ cancel: true }))
    await fixture.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent('<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'"></head><body></body></html>'))

    for (const sample of cases) {
      await access(path.join(process.env.WINDIR ?? 'C:\\Windows', 'Fonts', sample.fontFile))
      // The sandbox generates known pixels directly, not a window/screen
      // screenshot. Canvas resolution is fixed independently of desktop DPI.
      const dataUrl = await fixture.webContents.executeJavaScript(`(() => {
        const sample = ${JSON.stringify(sample)};
        const canvas = document.createElement('canvas'); canvas.width = 1200; canvas.height = 280;
        const context = canvas.getContext('2d');
        context.fillStyle = '#ffffff'; context.fillRect(0, 0, canvas.width, canvas.height);
        context.fillStyle = '#000000'; context.textBaseline = 'alphabetic';
        context.font = '64px "' + sample.font + '"'; context.fillText(sample.lines[0], 60, 100);
        context.font = '56px Arial'; context.fillText(sample.lines[1], 60, 220);
        return canvas.toDataURL('image/png');
      })()`)
      assert.match(dataUrl, /^data:image\/png;base64,/u)
      const canonical = decodeVisionImage(Buffer.from(dataUrl.slice(dataUrl.indexOf(',') + 1), 'base64'), nativeImage)
      assert.deepEqual([canonical.width, canonical.height], [1200, 280])
      const selection = images.importCanonical(owner.chatId, canonical, 'synthetic-ocr.png', { kind: 'file', label: 'synthetic-ocr.png' })
      assert.equal(selection.cancelled, false)
      const imageId = selection.state.items.at(-1).imageId
      const pending = analysis.runOcr({ chatId: owner.chatId, imageId, language: sample.language })
      assert.equal(analysis.getState(owner.chatId).busy, true)
      const completed = await pending
      assert.equal(completed.ocr.error, null, 'Real OCR evidence must survive the public analysis protocol.')
      assert.equal(completed.busy, false)
      assert.equal(completed.image.imageId, imageId)
      assert.equal(completed.image.sha256, canonical.sha256)
      const result = completed.ocr.result
      assert.ok(result)
      assert.equal(normalized(result.text), normalized(sample.lines.join('\n')), `Unexpected ${sample.language} recognition: ${result.text}`)
      assert.equal(result.language, sample.language)
      assert.equal(result.truncated, false)
      assert.deepEqual([result.width, result.height], [canonical.width, canonical.height])
      verifyBoxes(result, nativeImage.createFromBuffer(canonical.pngBytes).toBitmap())
      assert.equal(adapter.isBusy(), false, 'Native OCR must drain before the next operation.')
      assert.equal(analysis.hasActiveWork(), false)
      assert.deepEqual(analysis.getState(owner.chatId).ocr.result, result)
      process.stdout.write('VISION_OCR_NATIVE_RESULT:' + JSON.stringify({ version: readiness.version, language: result.language,
        text: result.text, words: result.words.length, width: result.width, height: result.height,
        minimumConfidence: Math.min(...result.words.map((word) => word.confidence)), packaged: Boolean(process.env.ELYSIA_OCR_PACKAGE_ASAR) }) + '\n')
    }
    analysis.shutdown()
    images.shutdown()
    fixture.destroy()
    assert.equal(BrowserWindow.getAllWindows().length, 0)
    process.stdout.write('VISION_OCR_NATIVE_SMOKE_OK\n')
    app.exit(0)
  } catch (error) {
    fixture?.destroy()
    process.stderr.write(`Synthetic real OCR acceptance failed: ${String(error?.stack ?? error)}\n`)
    app.exit(1)
  }
}).catch((error) => { process.stderr.write(String(error?.stack ?? error)); app.exit(1) })
