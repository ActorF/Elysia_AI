/** Verify local OCR limits, positional evidence, fixed process arguments, and actual child draining. */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { PassThrough } from 'node:stream'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

const moduleUrl = process.env.ELYSIA_VISION_OCR_TEST_MODULE
  ? pathToFileURL(process.env.ELYSIA_VISION_OCR_TEST_MODULE)
  : new URL('../dist-electron/vision-ocr.js', import.meta.url)
const { TesseractOcrAdapter, OcrError } = await import(moduleUrl.href)
const HEADER = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext'
const OUTPUT = `${HEADER}\n1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t\n5\t1\t1\t1\t1\t1\t2\t3\t40\t20\t96.5\t你好\n5\t1\t1\t1\t1\t2\t44\t3\t40\t20\t88\tHello\n`

function png(width = 100, height = 100) {
  const value = Buffer.alloc(33)
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(value)
  value.writeUInt32BE(13, 8); value.write('IHDR', 12)
  value.writeUInt32BE(width, 16); value.writeUInt32BE(height, 20)
  return value
}

async function fixture(t, options = {}) {
  const directory = await mkdtemp(path.join(tmpdir(), 'elysia-ocr-native-'))
  const executable = path.join(directory, process.platform === 'win32' ? 'tesseract.exe' : 'tesseract')
  await writeFile(executable, 'Synthetic inert runtime, never executed.')
  t.after(async () => {
    assert.equal(path.dirname(directory), path.resolve(tmpdir()))
    assert.ok(path.basename(directory).startsWith('elysia-ocr-native-'))
    await rm(directory, { recursive: true, force: true })
  })
  const calls = [], children = [], killed = []
  const native = { spawn: (command, args, spawnOptions) => {
    calls.push({ command, args, options: spawnOptions })
    const child = Object.assign(new EventEmitter(), { stdin: new PassThrough(), stdout: new PassThrough(),
      stderr: new PassThrough(), exitCode: null, signalCode: null })
    child.kill = (signal) => { killed.push(signal); return true }
    child.close = (code = 0) => { child.exitCode = code; child.emit('close', code) }
    child.input = []
    child.stdin.on('data', (chunk) => { child.input.push(Buffer.from(chunk)) })
    child.stdin.once('finish', () => {
      queueMicrotask(() => {
        if (args.includes('--version')) { child.stdout.write(options.version ?? 'tesseract 5.5.0\n'); child.close() }
        else if (args.includes('--list-langs')) { child.stdout.write(options.languages ?? 'List of available languages in "private path" (3):\neng\nchi_sim\nchi_tra\n'); child.close() }
        else if (!options.hold) { child.stdout.write(options.output ?? OUTPUT); child.close(options.code ?? 0) }
      })
    })
    children.push(child)
    return child
  } }
  const adapter = new TesseractOcrAdapter({ executable, native, timeoutMs: options.timeoutMs ?? 1000,
    ...(options.tessdataDirectory === undefined ? {} : { tessdataDirectory: options.tessdataDirectory }) })
  return { adapter, executable, calls, children, killed, directory }
}

async function waitFor(predicate) {
  const deadline = performance.now() + 1000
  while (!predicate()) {
    assert.ok(performance.now() < deadline)
    await new Promise((resolve) => setTimeout(resolve, 1))
  }
}

test('Readiness only reports installed closed languages and safe version; no installer or image runs', async (t) => {
  const { adapter, calls } = await fixture(t)
  assert.deepEqual(await adapter.readiness(), { status: 'ready', languages: ['eng', 'chi_sim', 'chi_tra'], version: '5.5.0' })
  assert.deepEqual(calls.map((call) => call.args), [['--version'], ['--list-langs']])
  assert.equal(adapter.isBusy(), false)
  const missing = new TesseractOcrAdapter({ executable: null })
  assert.deepEqual(await missing.readiness(), { status: 'missing_engine', languages: [], version: null })
})

test('Recognition returns Chinese and English word positions with normalized confidence using fixed hidden stdin/stdout', async (t) => {
  const { adapter, calls, children } = await fixture(t)
  const bytes = png()
  const result = await adapter.run(bytes, 100, 100, 'chi_sim+eng', new AbortController().signal)
  assert.deepEqual(result, { text: '你好 Hello', words: [
    { text: '你好', x: 2, y: 3, width: 40, height: 20, confidence: 0.965 },
    { text: 'Hello', x: 44, y: 3, width: 40, height: 20, confidence: 0.88 },
  ], language: 'chi_sim+eng', width: 100, height: 100, truncated: false })
  assert.deepEqual(calls[2].args, ['stdin', 'stdout', '--oem', '1', '--psm', '11', '-l', 'chi_sim+eng', 'tsv'])
  assert.equal(calls[2].options.shell, false)
  assert.equal(calls[2].options.windowsHide, true)
  assert.equal(calls[2].options.detached, false)
  assert.equal(calls[2].options.env.OMP_THREAD_LIMIT, '1')
  assert.equal(calls[2].options.env.TESSDATA_PREFIX, undefined)
  assert.deepEqual(Buffer.concat(children[2].input), bytes)
})

test('Missing required language, unsafe runtime, mismatched PNG and arbitrary arguments never start recognition', async (t) => {
  const { adapter, calls } = await fixture(t, { languages: 'eng\n' })
  await assert.rejects(adapter.run(png(), 100, 100, 'chi_tra+eng', new AbortController().signal), (error) => error.code === 'unavailable')
  assert.equal(calls.length, 2)
  for (const [bytes, width, height, language] of [[png(), 99, 100, 'eng'], [Buffer.from('not PNG'), 100, 100, 'eng'],
    [png(), 100, 100, 'eng --user-words private.txt'], [png(), true, 100, 'eng'], [png(), 100, 100, 'powershell']]) {
    await assert.rejects(adapter.run(bytes, width, height, language, new AbortController().signal), OcrError)
  }
  assert.equal(calls.length, 2)
  for (const executable of ['relative.exe', '\\\\server\\share\\tesseract.exe', 'C:\\file.exe:stream']) {
    const unsafe = new TesseractOcrAdapter({ executable })
    const state = await unsafe.readiness()
    assert.notEqual(state.status, 'ready')
    assert.equal(JSON.stringify(state).includes(executable), false)
  }
})

test('Cancellation requests owned-child kill and remains busy until actual close, denying a competing run', async (t) => {
  const { adapter, children, killed } = await fixture(t, { hold: true })
  const controller = new AbortController()
  const pending = adapter.run(png(), 100, 100, 'eng', controller.signal)
  let settled = false
  const checked = assert.rejects(pending, (error) => error.code === 'cancelled').finally(() => { settled = true })
  await waitFor(() => children.length === 3)
  controller.abort()
  await new Promise((resolve) => setTimeout(resolve, 5))
  assert.deepEqual(killed, ['SIGKILL'])
  assert.equal(settled, false)
  assert.equal(adapter.isBusy(), true)
  await assert.rejects(adapter.readiness(), (error) => error.code === 'busy')
  children[2].close(1)
  await checked
  assert.equal(adapter.isBusy(), false)
})

test('Timeout preserves physical ownership until the timed-out child closes', async (t) => {
  const { adapter, children, killed } = await fixture(t, { hold: true, timeoutMs: 60 })
  const pending = adapter.run(png(), 100, 100, 'eng', new AbortController().signal)
  const checked = assert.rejects(pending, (error) => error.code === 'timeout')
  await waitFor(() => killed.length === 1)
  assert.equal(adapter.isBusy(), true)
  children.at(-1).close(1)
  await checked
  assert.equal(adapter.isBusy(), false)
})

for (const [name, output] of [
  ['wrong header', 'text only'], ['out of bounds', OUTPUT.replace('\t44\t3\t40\t20\t88', '\t90\t3\t40\t20\t88')],
  ['invalid confidence', OUTPUT.replace('\t96.5\t', '\tNaN\t')], ['negative position', OUTPUT.replace('\t2\t3\t40', '\t-1\t3\t40')],
  ['duplicate word', OUTPUT + OUTPUT.split('\n')[2] + '\n'], ['invalid UTF8', Buffer.from([255, 254])],
]) {
  test(`Malformed ${name} is refused without exposing native output`, async (t) => {
    const { adapter } = await fixture(t, { output })
    await assert.rejects(adapter.run(png(), 100, 100, 'eng', new AbortController().signal), (error) => {
      assert.equal(error.code, 'invalid_output')
      assert.equal(error.message.includes('private'), false)
      return true
    })
    assert.equal(adapter.isBusy(), false)
  })
}

test('Word, text and stdout caps reject entire results instead of silently truncating evidence', async (t) => {
  const many = HEADER + '\n' + Array.from({ length: 2049 }, (_, index) => `5\t1\t1\t1\t1\t${index + 1}\t0\t0\t1\t1\t99\tx`).join('\n')
  for (const output of [many, OUTPUT.replace('你好', '中'.repeat(11000)), Buffer.alloc(1024 * 1024 + 1, 120)]) {
    const { adapter } = await fixture(t, { output })
    await assert.rejects(adapter.run(png(), 100, 100, 'eng', new AbortController().signal), (error) => error.code === 'limit_exceeded')
  }
})

test('An explicit tessdata directory becomes one fixed argument, never a command fragment', async (t) => {
  const first = await fixture(t)
  const directory = path.join(first.directory, 'tessdata')
  await mkdir(directory)
  const { adapter, calls } = await fixture(t, { tessdataDirectory: directory })
  await adapter.run(png(), 100, 100, 'eng', new AbortController().signal)
  assert.equal(calls[2].args.at(-1), 'tsv')
  assert.deepEqual(calls[2].args.slice(-3), ['--tessdata-dir', directory, 'tsv'])
})
