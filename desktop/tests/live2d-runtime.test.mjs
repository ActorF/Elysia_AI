/** Verify the fixed-asset PurismCore WebGL2 runtime without a browser. */

import assert from 'node:assert/strict'
import test from 'node:test'

import { createLive2DController } from '../src/character/live2d-runtime.ts'

const ORIGINAL_GLOBALS = {
  CustomEvent: globalThis.CustomEvent,
  ResizeObserver: globalThis.ResizeObserver,
  createImageBitmap: globalThis.createImageBitmap,
  document: globalThis.document,
  fetch: globalThis.fetch,
  window: globalThis.window,
}

const PARAMETER_IDS = [
  'ParamEyeLOpen',
  'ParamEyeROpen',
  'ParamEyeLSmile',
  'ParamEyeRSmile',
  'ParamMouthOpenY',
  'ParamMouthForm',
  'ParamCheek',
  'ParamAngleX',
  'ParamAngleY',
  'ParamAngleZ',
  'ParamBrowLY',
  'ParamBrowLForm',
  'ParamBrowRY',
  'ParamBrowRForm',
  'ParamHairFront',
  'ParamHairBack',
  'ParamHairFrontV',
  'ParamHairBackV',
  'ParamBodyAngleX',
  'ParamBodyAngleY',
  'ParamBodyAngleZ',
  'ParamBreath',
]

function makeStreamResponse(url, bytes, declaredLength = bytes.byteLength) {
  return {
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(bytes)
        controller.close()
      },
    }),
    headers: new Headers({ 'content-length': String(declaredLength) }),
    ok: true,
    redirected: false,
    status: 200,
    url,
  }
}

function makeFakeGl() {
  let lastIndexUpload = []
  const drawnIndices = []
  const deleted = []
  const uniform4fCalls = []
  const constants = {
    ARRAY_BUFFER: 0x8892,
    BACK: 0x0405,
    BLEND: 0x0be2,
    CCW: 0x0901,
    CLAMP_TO_EDGE: 0x812f,
    COLOR_BUFFER_BIT: 0x4000,
    COMPILE_STATUS: 0x8b81,
    CULL_FACE: 0x0b44,
    DEPTH_TEST: 0x0b71,
    DST_COLOR: 0x0306,
    ELEMENT_ARRAY_BUFFER: 0x8893,
    FLOAT: 0x1406,
    FRAGMENT_SHADER: 0x8b30,
    LINEAR: 0x2601,
    LINK_STATUS: 0x8b82,
    ONE: 1,
    ONE_MINUS_SRC_ALPHA: 0x0303,
    RGBA: 0x1908,
    SCISSOR_TEST: 0x0c11,
    STENCIL_TEST: 0x0b90,
    STREAM_DRAW: 0x88e0,
    TEXTURE0: 0x84c0,
    TEXTURE_2D: 0x0de1,
    TEXTURE_MAG_FILTER: 0x2800,
    TEXTURE_MIN_FILTER: 0x2801,
    TEXTURE_WRAP_S: 0x2802,
    TEXTURE_WRAP_T: 0x2803,
    TRIANGLES: 0x0004,
    UNPACK_FLIP_Y_WEBGL: 0x9240,
    UNPACK_PREMULTIPLY_ALPHA_WEBGL: 0x9241,
    UNSIGNED_BYTE: 0x1401,
    UNSIGNED_SHORT: 0x1403,
    VERTEX_SHADER: 0x8b31,
    ZERO: 0,
  }
  const gl = {
    ...constants,
    activeTexture() {},
    attachShader() {},
    bindBuffer() {},
    bindTexture() {},
    bindVertexArray() {},
    blendFuncSeparate() {},
    bufferData(target, data) {
      if (target === constants.ELEMENT_ARRAY_BUFFER) {
        lastIndexUpload = Array.from(data)
      }
    },
    clear() {},
    clearColor() {},
    compileShader() {},
    createBuffer: () => ({}),
    createProgram: () => ({}),
    createShader: () => ({}),
    createTexture: () => ({}),
    createVertexArray: () => ({}),
    cullFace() {},
    deleteBuffer(value) { deleted.push(value) },
    deleteProgram(value) { deleted.push(value) },
    deleteShader() {},
    deleteTexture(value) { deleted.push(value) },
    deleteVertexArray(value) { deleted.push(value) },
    disable() {},
    drawElements() { drawnIndices.push(lastIndexUpload) },
    enable() {},
    enableVertexAttribArray() {},
    frontFace() {},
    getAttribLocation(_program, name) { return name === 'a_position' ? 0 : 1 },
    getProgramInfoLog: () => '',
    getProgramParameter: () => true,
    getShaderInfoLog: () => '',
    getShaderParameter: () => true,
    getUniformLocation: (_program, name) => ({ name }),
    linkProgram() {},
    pixelStorei() {},
    shaderSource() {},
    texImage2D() {},
    texParameteri() {},
    uniform1i() {},
    uniform4f(location, ...values) {
      uniform4fCalls.push({
        name: location?.name,
        values,
      })
    },
    useProgram() {},
    vertexAttribPointer() {},
    viewport() {},
  }
  return { deleted, drawnIndices, gl, uniform4fCalls }
}

function installEnvironment({
  canvasBounds = { height: 300, width: 200 },
  manifestOverride,
  packaged = false,
  webgl = true,
} = {}) {
  const animationFrames = new Map()
  const canvasListeners = new Map()
  const requestedUrls = []
  const {
    deleted,
    drawnIndices,
    gl,
    uniform4fCalls,
  } = makeFakeGl()
  const parameterValues = new Float32Array(PARAMETER_IDS.length)
  parameterValues[PARAMETER_IDS.indexOf('ParamEyeLOpen')] = 1
  parameterValues[PARAMETER_IDS.indexOf('ParamEyeROpen')] = 1
  const maximumValues = new Float32Array(PARAMETER_IDS.length).fill(30)
  const minimumValues = new Float32Array(PARAMETER_IDS.length).fill(-30)
  let nextFrame = 1
  let modelReleased = false
  let modelError = 0
  let mocReleased = false

  const drawables = {
    count: 2,
    constantFlags: new Uint8Array([4, 4]),
    dynamicFlags: new Uint8Array([1, 1]),
    indexCounts: new Int32Array([3, 3]),
    indices: [new Uint16Array([0, 1, 2]), new Uint16Array([0, 2, 1])],
    maskCounts: new Int32Array([0, 0]),
    multiplyColors: new Float32Array([1, 1, 1, 1, 1, 1, 1, 1]),
    opacities: new Float32Array([1, 1]),
    resetDynamicFlags() { this.dynamicFlags.fill(0) },
    screenColors: new Float32Array(8),
    textureIndices: new Int32Array([0, 0]),
    vertexCounts: new Int32Array([3, 3]),
    vertexPositions: [
      new Float32Array([-1, -1, 1, -1, 0, 1]),
      new Float32Array([-.5, -.5, .5, -.5, 0, .5]),
    ],
    vertexUvs: [
      new Float32Array([0, 0, 1, 0, .5, 1]),
      new Float32Array([0, 0, 1, 0, .5, 1]),
    ],
  }
  const model = {
    canvasinfo: {
      CanvasHeight: 1000,
      CanvasOriginX: 500,
      CanvasOriginY: 500,
      CanvasWidth: 1000,
      PixelsPerUnit: 500,
    },
    drawables,
    getDrawableRenderOrders: () => new Int32Array([1, 0]),
    getLastError: () => modelError,
    parameters: {
      count: PARAMETER_IDS.length,
      defaultValues: new Float32Array(PARAMETER_IDS.length),
      ids: PARAMETER_IDS,
      maximumValues,
      minimumValues,
      values: parameterValues,
    },
    release() { modelReleased = true },
    update() { drawables.dynamicFlags.fill(1) },
  }
  const moc = {
    _release() { mocReleased = true },
    hasMocConsistency: () => 1,
  }
  const core = {
    Memory: { initializeAmountOfMemory() {} },
    Moc: { fromArrayBuffer: () => moc },
    Model: { fromMoc: () => model },
    Version: { csmGetVersion: () => 0x05010000 },
  }
  const windowListeners = new Map()
  const fakeWindow = {
    PurismCore: core,
    addEventListener(type, listener) { windowListeners.set(type, listener) },
    cancelAnimationFrame(id) { animationFrames.delete(id) },
    devicePixelRatio: 1.5,
    location: packaged
      ? { origin: 'null', protocol: 'file:' }
      : { origin: 'https://elysia.test', protocol: 'https:' },
    removeEventListener(type) { windowListeners.delete(type) },
    requestAnimationFrame(callback) {
      const id = nextFrame
      nextFrame += 1
      animationFrames.set(id, callback)
      return id
    },
  }
  const rootDataset = { characterMouth: 'wide' }
  const fakeDocument = {
    baseURI: 'https://elysia.test/',
    documentElement: { dataset: rootDataset },
    head: { append() { throw new Error('Core script must not be injected in this test.') } },
  }
  const canvas = {
    dataset: {},
    height: 0,
    width: 0,
    addEventListener(type, listener) { canvasListeners.set(type, listener) },
    dispatchEvent(event) {
      canvasListeners.get(event.type)?.(event)
      return true
    },
    getBoundingClientRect: () => canvasBounds,
    getContext: () => (webgl ? gl : null),
    removeEventListener(type) { canvasListeners.delete(type) },
  }
  const defaultManifest = {
    FileReferences: {
      Moc: 'model.moc3',
      Textures: ['textures/atlas.png'],
    },
    Version: 3,
  }
  const manifest = manifestOverride ?? defaultManifest
  const manifestBytes = new TextEncoder().encode(JSON.stringify(manifest))
  const mocBytes = new Uint8Array([0x4d, 0x4f, 0x43, 0x33, 1])
  const textureBytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

  globalThis.CustomEvent ??= class CustomEvent extends Event {
    constructor(type, init = {}) {
      super(type)
      this.detail = init.detail
    }
  }
  globalThis.ResizeObserver = class ResizeObserver {
    disconnect() {}
    observe() {}
  }
  globalThis.createImageBitmap = async () => ({
    close() {},
    height: 512,
    width: 512,
  })
  globalThis.document = fakeDocument
  globalThis.window = fakeWindow
  globalThis.fetch = async (input) => {
    const url = input instanceof URL ? input.href : String(input)
    requestedUrls.push(url)
    if (url.endsWith('/model.model3.json')) {
      return makeStreamResponse(url, manifestBytes)
    }
    if (url.endsWith('/model.moc3')) {
      return makeStreamResponse(url, mocBytes)
    }
    if (url.endsWith('/textures/atlas.png')) {
      return makeStreamResponse(url, textureBytes)
    }
    throw new Error(`Unexpected URL: ${url}`)
  }

  return {
    animationFrames,
    canvas,
    canvasListeners,
    deleted,
    drawnIndices,
    get modelReleased() { return modelReleased },
    get mocReleased() { return mocReleased },
    maximumValues,
    minimumValues,
    parameterValues,
    requestedUrls,
    rootDataset,
    setModelError(value) { modelError = value },
    uniform4fCalls,
  }
}

test('loads only fixed assets and renders every drawable in render order', async () => {
  const environment = installEnvironment()
  const controller = await createLive2DController(environment.canvas, {
    emotion: 'happy',
    framing: 'half-body',
    state: 'speaking',
  })

  assert.deepEqual(environment.requestedUrls.sort(), [
    'https://elysia.test/character/live2d/elysia/model.moc3',
    'https://elysia.test/character/live2d/elysia/model.model3.json',
    'https://elysia.test/character/live2d/elysia/textures/atlas.png',
  ])
  assert.equal(environment.canvas.dataset.live2dStatus, 'ready')
  assert.equal(environment.canvas.width, 300)
  assert.equal(environment.canvas.height, 450)

  const [queuedFrameId, queuedFrame] = [...environment.animationFrames.entries()][0]
  assert.equal(typeof queuedFrame, 'function')
  // Browsers consume a RAF entry before invoking it; mirror that behavior so
  // a manually executed callback cannot survive as a stale test-only owner.
  environment.animationFrames.delete(queuedFrameId)
  queuedFrame(performance.now() + 1000)
  assert.deepEqual(environment.drawnIndices, [[0, 2, 1], [0, 1, 2]])
  assert.ok(
    environment.parameterValues[PARAMETER_IDS.indexOf('ParamMouthOpenY')] > 0.9,
    'the trusted root mouth cue should drive the speaking parameter',
  )

  controller.setState('idle')
  controller.setEmotion('neutral')

  let defaultPrevented = false
  const makeBitmap = () => ({
    close() {},
    height: 512,
    width: 512,
  })
  let resolveRestoredTexture
  globalThis.createImageBitmap = () => new Promise((resolve) => {
    resolveRestoredTexture = resolve
  })
  environment.canvasListeners.get('webglcontextlost')({
    preventDefault() { defaultPrevented = true },
  })
  assert.equal(defaultPrevented, true)
  assert.equal(environment.canvas.dataset.live2dStatus, 'recovering')
  environment.canvasListeners.get('webglcontextrestored')()
  environment.canvasListeners.get('webglcontextlost')({ preventDefault() {} })
  resolveRestoredTexture(makeBitmap())
  await new Promise((resolve) => { setImmediate(resolve) })
  assert.equal(
    environment.canvas.dataset.live2dStatus,
    'recovering',
    'an obsolete restore must not restart a context that was lost again',
  )
  globalThis.createImageBitmap = async () => makeBitmap()
  environment.canvasListeners.get('webglcontextrestored')()
  await new Promise((resolve) => { setImmediate(resolve) })
  assert.equal(environment.canvas.dataset.live2dStatus, 'ready')

  controller.dispose()
  controller.dispose()
  assert.equal(environment.modelReleased, true)
  assert.equal(environment.mocReleased, true)
  assert.ok(environment.deleted.length > 0)
  assert.equal('live2dStatus' in environment.canvas.dataset, false)

  environment.maximumValues[0] = Number.NaN
  await assert.rejects(
    createLive2DController(environment.canvas, {
      emotion: 'neutral',
      framing: 'half-body',
      state: 'idle',
    }),
    /invalid bounds or values/,
  )
  environment.maximumValues[0] = 30

  const fatalController = await createLive2DController(environment.canvas, {
    emotion: 'neutral',
    framing: 'half-body',
    state: 'idle',
  })
  let runtimeError
  environment.canvas.addEventListener('elysia:live2d-error', (event) => {
    runtimeError = event.detail
  })
  environment.setModelError(1)
  const [fatalFrameId, fatalFrame] = [...environment.animationFrames.entries()][0]
  environment.animationFrames.delete(fatalFrameId)
  fatalFrame(performance.now() + 2000)
  assert.equal(environment.canvas.dataset.live2dStatus, 'error')
  assert.match(runtimeError?.message ?? '', /error while updating/)
  assert.equal(environment.animationFrames.size, 0)
  environment.setModelError(0)
  fatalController.dispose()
})

test('rejects a manifest that tries to select a different resource', async () => {
  const environment = installEnvironment({
    manifestOverride: {
      FileReferences: {
        Moc: '../outside.moc3',
        Textures: ['https://example.com/atlas.png'],
      },
      Version: 3,
    },
  })
  await assert.rejects(
    createLive2DController(environment.canvas, {
      emotion: 'neutral',
      framing: 'half-body',
      state: 'idle',
    }),
    /fixed asset contract/,
  )
  assert.equal(environment.canvas.dataset.live2dStatus, 'error')
})

test('uses only the allowlisted custom protocol in a packaged file page', async () => {
  const environment = installEnvironment({ packaged: true })
  const controller = await createLive2DController(environment.canvas, {
    emotion: 'neutral',
    framing: 'half-body',
    state: 'idle',
  })
  assert.deepEqual(environment.requestedUrls.sort(), [
    'elysia-asset://character/live2d/elysia/model.moc3',
    'elysia-asset://character/live2d/elysia/model.model3.json',
    'elysia-asset://character/live2d/elysia/textures/atlas.png',
  ])
  controller.dispose()
})

test('fails before loading assets when WebGL2 is unavailable', async () => {
  const environment = installEnvironment({ webgl: false })
  await assert.rejects(
    createLive2DController(environment.canvas, {
      emotion: 'neutral',
      framing: 'half-body',
      state: 'idle',
    }),
    /WebGL2 is unavailable/,
  )
  assert.deepEqual(environment.requestedUrls, [])
})

test('uses the reviewed half-body and full-body camera transforms', async () => {
  const cases = [
    {
      expected: [2.325581, 1.550388, 0, -0.883721],
      framing: 'half-body',
    },
    {
      expected: [2.325581, 1.550388, 0, -0.666667],
      framing: 'call-half-body',
    },
    {
      expected: [1.485149, 0.990099, 0, -0.009901],
      framing: 'full-body',
    },
  ]

  for (const { expected, framing } of cases) {
    const environment = installEnvironment()
    const controller = await createLive2DController(environment.canvas, {
      emotion: 'neutral',
      framing,
      state: 'idle',
    })
    const [frameId, frame] = [...environment.animationFrames.entries()][0]
    environment.animationFrames.delete(frameId)
    frame(performance.now() + 1000)

    const transform = environment.uniform4fCalls.find(
      ({ name }) => name === 'u_transform',
    )?.values
    assert.ok(transform, `${framing} transform must be uploaded`)
    assert.deepEqual(
      transform?.map((value) => Number(value.toFixed(6))),
      expected,
      framing,
    )
    controller.dispose()
  }
})

test('main half-body camera keeps the reviewed head-to-waist viewport', async () => {
  const environment = installEnvironment({
    canvasBounds: { height: 274, width: 281 },
  })
  const controller = await createLive2DController(environment.canvas, {
    emotion: 'neutral',
    framing: 'half-body',
    state: 'idle',
  })
  const [frameId, frame] = [...environment.animationFrames.entries()][0]
  environment.animationFrames.delete(frameId)
  frame(performance.now() + 1000)

  const transform = environment.uniform4fCalls.find(
    ({ name }) => name === 'u_transform',
  )?.values
  assert.ok(transform, 'half-body transform must be uploaded')
  const [scaleX, scaleY, _translateX, translateY] = transform
  const visibleWidth = 2 / scaleX
  const visibleBottom = (-1 - translateY) / scaleY
  const visibleTop = (1 - translateY) / scaleY
  assert.ok(visibleWidth >= 0.85 && visibleWidth <= 0.87, visibleWidth)
  assert.ok(visibleBottom >= 0.14 && visibleBottom <= 0.16, visibleBottom)
  assert.ok(visibleTop >= 0.98 && visibleTop <= 1, visibleTop)
  controller.dispose()
})

test('rejects unsupported framing before requesting model assets', async () => {
  const environment = installEnvironment()

  await assert.rejects(
    createLive2DController(environment.canvas, {
      emotion: 'neutral',
      framing: 'portrait',
      state: 'idle',
    }),
    /Unsupported Live2D character framing: portrait/,
  )
  assert.deepEqual(environment.requestedUrls, [])
  assert.equal('live2dStatus' in environment.canvas.dataset, false)
})

test.after(() => {
  for (const [name, value] of Object.entries(ORIGINAL_GLOBALS)) {
    if (value === undefined) {
      delete globalThis[name]
    } else {
      globalThis[name] = value
    }
  }
})
