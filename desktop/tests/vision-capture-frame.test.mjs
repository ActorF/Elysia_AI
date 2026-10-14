/** Exercise the actual static capture-frame script in an inert browser VM, including crop bounds and track cleanup before publication. */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import vm from 'node:vm'
import test from 'node:test'

const script = await fs.readFile(new URL('../public/vision-capture-frame.js', import.meta.url), 'utf8')
function fixture({ region = null, width = 100, height = 50, fail = null, audio = false, singleFrameDuringPlay = false } = {}) {
  const events = [], calls = [], stopped = []
  let firstFrameCallback = null
  const tracks = [{ stop: () => { stopped.push(true); events.push('stop') } }]
  const stream = { getTracks: () => tracks, getAudioTracks: () => audio ? tracks : [], getVideoTracks: () => tracks }
  const video = { videoWidth: width, videoHeight: height, muted: false, srcObject: null,
    play: async () => {
      if (fail === 'play') throw new Error('native-secret')
      if (singleFrameDuringPlay) { events.push('first-frame'); firstFrameCallback?.() }
    }, pause: () => events.push('pause'),
    requestVideoFrameCallback: callback => { if (singleFrameDuringPlay) firstFrameCallback = callback; else callback(); return 1 } }
  const canvas = { width: 0, height: 0,
    getContext: () => fail === 'canvas' ? null : { drawImage: (...arguments_) => calls.push(arguments_) },
    toBlob: callback => callback(fail === 'blob' ? null : { size: 64, arrayBuffer: async () => new Uint8Array(64).fill(8).buffer }) }
  const context = vm.createContext({ Uint8Array, Promise, Number, Math, Error,
    navigator: { mediaDevices: { getDisplayMedia: async options => { events.push('capture'); calls.push(options); if (fail === 'media') throw new Error('secret-path'); return stream } } },
    document: { createElement: kind => kind === 'video' ? video : canvas },
    elysiaPrivateVisionCapture: { configuration: () => ({ region }),
      finish: async value => { assert.equal(stopped.length, 1); assert.equal(video.srcObject, null); assert.equal(canvas.width, 0);
        events.push('finish'); calls.push({ frame: { ...value, bytes: Uint8Array.from(value.bytes) } }) },
      failure: async () => { events.push('failure') } } })
  vm.runInContext(script, context)
  return { context, events, calls, stopped, video, canvas }
}

test('One full-source frame stops every track before sending pixels and never requests audio or another source', async () => {
  const f = fixture()
  await f.context.elysiaCaptureStart()
  assert.equal(f.calls[0].audio, false); assert.equal(f.calls[0].video.frameRate, 1)
  assert.equal(Object.hasOwn(f.calls[0].video, 'deviceId'), false)
  assert.deepEqual(f.events, ['capture', 'stop', 'pause', 'finish'])
  assert.equal(f.calls.at(-1).frame.width, 100); assert.equal(f.calls.at(-1).frame.height, 50)
  await f.context.elysiaCaptureStart()
  assert.equal(f.events.filter(event => event === 'capture').length, 1)
})

test('A static source delivering its only frame during play completes without waiting for a second frame', async () => {
  const f = fixture({ singleFrameDuringPlay: true })
  const running = f.context.elysiaCaptureStart()
  // This source never emits another frame: registering after play would leave
  // the helper waiting forever, even though its first frame is already decoded.
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.events.at(-1), 'finish', 'The first submitted frame must be observed before play resolves.')
  await running
  assert.deepEqual(f.events, ['capture', 'first-frame', 'stop', 'pause', 'finish'])
  assert.equal(f.events.filter(event => event === 'capture').length, 1)
})

test('Normalized display crops round inward and publish only the approved rectangle after one whole source frame', async () => {
  const f = fixture({ region: { x: 0.105, y: 0.105, width: 0.5, height: 0.5 } })
  await f.context.elysiaCaptureStart()
  const draw = f.calls[1]
  assert.deepEqual(draw.slice(1), [11, 6, 49, 24, 0, 0, 49, 24])
  assert.equal(f.calls.at(-1).frame.width, 49); assert.equal(f.calls.at(-1).frame.height, 24)
  assert.equal(f.events.filter(event => event === 'capture').length, 1)
})

test('Decoder/frame/canvas failures, overlarge canvases and unexpected audio clean tracks and publish no image', async () => {
  for (const options of [{ fail: 'play' }, { fail: 'canvas' }, { fail: 'blob' }, { width: 8192, height: 8192 }, { audio: true },
    { region: { x: 0, y: 0, width: 0.001, height: 0.001 } }]) {
    const f = fixture(options)
    await f.context.elysiaCaptureStart()
    assert.equal(f.stopped.length, 1); assert.equal(f.events.at(-1), 'failure')
    assert.equal(f.events.includes('finish'), false)
  }
  const f = fixture({ fail: 'media' })
  await f.context.elysiaCaptureStart()
  assert.deepEqual(f.events, ['capture', 'failure'])
})
