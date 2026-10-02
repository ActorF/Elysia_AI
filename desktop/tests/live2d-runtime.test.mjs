/** Verify the bounded generic PurismCore WebGL2 runtime without a browser. */

import assert from 'node:assert/strict'
import test from 'node:test'

import { createLive2DController } from '../src/character/live2d-runtime.ts'

const SAME_ORIGIN_MODEL_MANIFEST_URL = 'https://elysia.test/models/cat.model3.json'

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
  'ParamBodyAngleX',
  'ParamBodyAngleY',
  'ParamBodyAngleZ',
  'ParamBreath',
  'ParamPhysicsOut',
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

function makePngHeader(width = 512, height = 512) {
  const bytes = new Uint8Array(33)
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0)
  bytes[11] = 13
  bytes.set([0x49, 0x48, 0x44, 0x52], 12)
  new DataView(bytes.buffer).setUint32(16, width)
  new DataView(bytes.buffer).setUint32(20, height)
  bytes[24] = 8
  bytes[25] = 6
  return bytes
}

function makePhysicsDocument() {
  return {
    Meta: {
      EffectiveForces: {
        Gravity: { X: 0, Y: -1 },
        Wind: { X: 0, Y: 0 },
      },
      PhysicsSettingCount: 1,
      TotalInputCount: 1,
      TotalOutputCount: 1,
      VertexCount: 2,
    },
    PhysicsSettings: [{
      Input: [{
        Reflect: false,
        Source: { Id: 'ParamAngleX', Target: 'Parameter' },
        Type: 'X',
        Weight: 100,
      }],
      Normalization: {
        Angle: { Default: 0, Maximum: 30, Minimum: -30 },
        Position: { Default: 0, Maximum: 30, Minimum: -30 },
      },
      Output: [{
        Destination: { Id: 'ParamPhysicsOut', Target: 'Parameter' },
        Reflect: false,
        Scale: 30,
        Type: 'Angle',
        VertexIndex: 1,
        Weight: 100,
      }],
      Vertices: [
        {
          Acceleration: 1,
          Delay: 1,
          Mobility: 1,
          Position: { X: 0, Y: 0 },
          Radius: 0,
        },
        {
          Acceleration: 1,
          Delay: 1,
          Mobility: 0.8,
          Position: { X: 0, Y: 10 },
          Radius: 10,
        },
      ],
    }],
    Version: 3,
  }
}

function makeFakeGl() {
  let lastIndexUpload = []
  const drawnIndices = []
  const maskDrawnIndices = []
  const deleted = []
  const uniform1iCalls = []
  const uniform4fCalls = []
  let framebuffer = null
  const constants = {
    ARRAY_BUFFER: 0x8892,
    BACK: 0x0405,
    BLEND: 0x0be2,
    CCW: 0x0901,
    CLAMP_TO_EDGE: 0x812f,
    COLOR_BUFFER_BIT: 0x4000,
    COLOR_ATTACHMENT0: 0x8ce0,
    COMPILE_STATUS: 0x8b81,
    CULL_FACE: 0x0b44,
    DEPTH_TEST: 0x0b71,
    DST_COLOR: 0x0306,
    ELEMENT_ARRAY_BUFFER: 0x8893,
    FLOAT: 0x1406,
    FRAMEBUFFER: 0x8d40,
    FRAMEBUFFER_COMPLETE: 0x8cd5,
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
    bindFramebuffer(_target, value) { framebuffer = value },
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
    checkFramebufferStatus: () => constants.FRAMEBUFFER_COMPLETE,
    compileShader() {},
    createBuffer: () => ({}),
    createFramebuffer: () => ({}),
    createProgram: () => ({}),
    createShader: () => ({}),
    createTexture: () => ({}),
    createVertexArray: () => ({}),
    cullFace() {},
    deleteBuffer(value) { deleted.push(value) },
    deleteFramebuffer(value) { deleted.push(value) },
    deleteProgram(value) { deleted.push(value) },
    deleteShader() {},
    deleteTexture(value) { deleted.push(value) },
    deleteVertexArray(value) { deleted.push(value) },
    disable() {},
    drawElements() {
      ;(framebuffer === null ? drawnIndices : maskDrawnIndices)
        .push(lastIndexUpload)
    },
    enable() {},
    enableVertexAttribArray() {},
    framebufferTexture2D() {},
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
    uniform1f() {},
    uniform1i(location, value) {
      uniform1iCalls.push({ name: location?.name, value })
    },
    uniform2f() {},
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
  return {
    deleted,
    drawnIndices,
    gl,
    maskDrawnIndices,
    uniform1iCalls,
    uniform4fCalls,
  }
}

function installEnvironment({
  canvasBounds = { height: 300, width: 200 },
  drawableOpacities = [1, 1],
  manifestOverride,
  mocDeclaredLength,
  physicsDocument,
  textureDeclaredLength,
  textureDimensions = [[512, 512], [512, 512]],
  webgl = true,
} = {}) {
  const animationFrames = new Map()
  const canvasListeners = new Map()
  const requestedUrls = []
  const {
    deleted,
    drawnIndices,
    gl,
    maskDrawnIndices,
    uniform1iCalls,
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
  let bitmapDecodeCount = 0

  const drawables = {
    count: 2,
    constantFlags: new Uint8Array([12, 4]),
    dynamicFlags: new Uint8Array([1, 1]),
    indexCounts: new Int32Array([3, 3]),
    indices: [new Uint16Array([0, 1, 2]), new Uint16Array([0, 2, 1])],
    maskCounts: new Int32Array([1, 0]),
    masks: [new Int32Array([1]), new Int32Array(0)],
    multiplyColors: new Float32Array([1, 1, 1, 1, 1, 1, 1, 1]),
    opacities: new Float32Array(drawableOpacities),
    resetDynamicFlags() { this.dynamicFlags.fill(0) },
    screenColors: new Float32Array(8),
    textureIndices: new Int32Array([1, 0]),
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
    location: { origin: 'https://elysia.test', protocol: 'https:' },
    removeEventListener(type) { windowListeners.delete(type) },
    requestAnimationFrame(callback) {
      const id = nextFrame
      nextFrame += 1
      animationFrames.set(id, callback)
      return id
    },
  }
  const fakeDocument = {
    baseURI: 'https://elysia.test/',
    documentElement: { dataset: {} },
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
      ...(physicsDocument === undefined ? {} : { Physics: 'cat.physics3.json' }),
      Textures: ['textures/face.png', 'textures/body.png'],
    },
    Version: 3,
  }
  const manifest = manifestOverride ?? defaultManifest
  const manifestBytes = new TextEncoder().encode(JSON.stringify(manifest))
  const mocBytes = new Uint8Array([0x4d, 0x4f, 0x43, 0x33, 1])
  const textureBytes = textureDimensions.map(([width, height]) => (
    makePngHeader(width, height)
  ))
  const physicsBytes = physicsDocument === undefined
    ? null
    : new TextEncoder().encode(JSON.stringify(physicsDocument))

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
  globalThis.createImageBitmap = async () => {
    const [width, height] = textureDimensions[
      bitmapDecodeCount % textureDimensions.length
    ]
    bitmapDecodeCount += 1
    return {
      close() {},
      height,
      width,
    }
  }
  globalThis.document = fakeDocument
  globalThis.window = fakeWindow
  globalThis.fetch = async (input) => {
    const url = input instanceof URL ? input.href : String(input)
    requestedUrls.push(url)
    if (url.endsWith('.model3.json')) {
      return makeStreamResponse(url, manifestBytes)
    }
    if (url.endsWith('/model.moc3')) {
      return makeStreamResponse(url, mocBytes, mocDeclaredLength)
    }
    if (url.endsWith('/cat.physics3.json') && physicsBytes !== null) {
      return makeStreamResponse(url, physicsBytes)
    }
    const textureIndex = url.endsWith('/textures/face.png')
      ? 0
      : url.endsWith('/textures/body.png') ? 1 : -1
    if (textureIndex >= 0) {
      return makeStreamResponse(url, textureBytes[textureIndex], textureDeclaredLength)
    }
    throw new Error(`Unexpected URL: ${url}`)
  }

  return {
    animationFrames,
    canvas,
    canvasListeners,
    deleted,
    drawnIndices,
    get bitmapDecodeCount() { return bitmapDecodeCount },
    maskDrawnIndices,
    get modelReleased() { return modelReleased },
    get mocReleased() { return mocReleased },
    maximumValues,
    minimumValues,
    parameterValues,
    requestedUrls,
    setModelError(value) { modelError = value },
    uniform1iCalls,
    uniform4fCalls,
  }
}

test('loads a selected multi-texture model with masks and optional parameters', async () => {
  // This first environment also supplies the module-cached fake Core to later
  // cases, so exercise non-opaque values here instead of creating a second
  // fake Core that the production cache would intentionally ignore.
  const environment = installEnvironment({
    drawableOpacities: [0.25, 0.625],
    physicsDocument: makePhysicsDocument(),
  })
  const controller = await createLive2DController(environment.canvas, {
    emotion: 'happy',
    framing: 'half-body',
    modelManifestUrl: 'https://elysia.test/selected/cat.model3.json',
    state: 'speaking',
  })

  assert.deepEqual(environment.requestedUrls.sort(), [
    'https://elysia.test/selected/cat.model3.json',
    'https://elysia.test/selected/cat.physics3.json',
    'https://elysia.test/selected/model.moc3',
    'https://elysia.test/selected/textures/body.png',
    'https://elysia.test/selected/textures/face.png',
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
  assert.deepEqual(environment.maskDrawnIndices, [[0, 2, 1]])
  assert.ok(
    environment.uniform1iCalls.some(
      ({ name, value }) => name === 'u_mask_inverted' && value === 1,
    ),
    'the Cubism inverted-mask flag must reach the composite shader',
  )
  const baseColors = environment.uniform4fCalls
    .filter(({ name }) => name === 'u_base_color')
    .map(({ values }) => values)
  assert.deepEqual(
    baseColors,
    [
      [0.625, 0.625, 0.625, 0.625],
      [0.25, 0.25, 0.25, 0.25],
    ],
    'render-order uploads must remain premultiplied during opacity crossfades',
  )
  assert.equal(
    environment.parameterValues[PARAMETER_IDS.indexOf('ParamMouthOpenY')],
    0,
    'the isolated pet must not consume a main-renderer speech cue',
  )
  assert.notEqual(
    environment.parameterValues[PARAMETER_IDS.indexOf('ParamPhysicsOut')],
    0,
    'the selected physics3 rig must update its destination parameter',
  )

  controller.setState('idle')
  controller.setEmotion('neutral')

  let defaultPrevented = false
  const makeBitmap = () => ({
    close() {},
    height: 512,
    width: 512,
  })
  const restoreResolvers = []
  globalThis.createImageBitmap = () => new Promise((resolve) => {
    restoreResolvers.push(resolve)
  })
  environment.canvasListeners.get('webglcontextlost')({
    preventDefault() { defaultPrevented = true },
  })
  assert.equal(defaultPrevented, true)
  assert.equal(environment.canvas.dataset.live2dStatus, 'recovering')
  environment.canvasListeners.get('webglcontextrestored')()
  environment.canvasListeners.get('webglcontextlost')({ preventDefault() {} })
  restoreResolvers.shift()(makeBitmap())
  await new Promise((resolve) => { setImmediate(resolve) })
  restoreResolvers.shift()(makeBitmap())
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
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
      state: 'idle',
    }),
    /invalid bounds or values/,
  )
  environment.maximumValues[0] = 30

  const fatalController = await createLive2DController(environment.canvas, {
    emotion: 'neutral',
    framing: 'half-body',
    modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
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
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
      state: 'idle',
    }),
    /Unsafe Live2D manifest file reference/,
  )
  assert.equal(environment.canvas.dataset.live2dStatus, 'error')
})

test('rejects the removed bundled-model protocol before requesting assets', async () => {
  const environment = installEnvironment()
  await assert.rejects(
    createLive2DController(environment.canvas, {
      emotion: 'neutral',
      framing: 'half-body',
      modelManifestUrl: 'elysia-asset://character/live2d/elysia/model.model3.json',
      state: 'idle',
    }),
    /Unsupported Live2D asset protocol: elysia-asset:/,
  )
  assert.deepEqual(environment.requestedUrls, [])
})

test('loads an explicitly selected opaque desktop-pet protocol manifest', async () => {
  const environment = installEnvironment()
  const controller = await createLive2DController(environment.canvas, {
    emotion: 'neutral',
    framing: 'full-body',
    modelManifestUrl: 'elysia-pet-asset://model/generation-7/model_0123456789abcdef0123456789abcdef/cat.model3.json',
    state: 'idle',
  })
  assert.deepEqual(environment.requestedUrls.sort(), [
    'elysia-pet-asset://model/generation-7/model_0123456789abcdef0123456789abcdef/cat.model3.json',
    'elysia-pet-asset://model/generation-7/model_0123456789abcdef0123456789abcdef/model.moc3',
    'elysia-pet-asset://model/generation-7/model_0123456789abcdef0123456789abcdef/textures/body.png',
    'elysia-pet-asset://model/generation-7/model_0123456789abcdef0123456789abcdef/textures/face.png',
  ])
  controller.dispose()
})

test('rejects cross-origin manifests and oversized model resources', async () => {
  const crossOrigin = installEnvironment()
  await assert.rejects(
    createLive2DController(crossOrigin.canvas, {
      emotion: 'neutral',
      framing: 'full-body',
      modelManifestUrl: 'https://untrusted.example/cat.model3.json',
      state: 'idle',
    }),
    /same-origin/,
  )
  assert.deepEqual(crossOrigin.requestedUrls, [])

  const wrongAuthority = installEnvironment()
  await assert.rejects(
    createLive2DController(wrongAuthority.canvas, {
      emotion: 'neutral',
      framing: 'full-body',
      modelManifestUrl: 'elysia-pet-asset://unselected/cat.model3.json',
      state: 'idle',
    }),
    /asset authority/,
  )
  assert.deepEqual(wrongAuthority.requestedUrls, [])

  const oversizedMoc = installEnvironment({
    mocDeclaredLength: 64 * 1024 * 1024 + 1,
  })
  await assert.rejects(
    createLive2DController(oversizedMoc.canvas, {
      emotion: 'neutral',
      framing: 'full-body',
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
      state: 'idle',
    }),
    /declared size limit/,
  )

  const oversizedTexture = installEnvironment({
    textureDeclaredLength: 16 * 1024 * 1024 + 1,
  })
  await assert.rejects(
    createLive2DController(oversizedTexture.canvas, {
      emotion: 'neutral',
      framing: 'full-body',
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
      state: 'idle',
    }),
    /declared size limit/,
  )
})

test('rejects oversized PNG dimensions before invoking an image decoder', async () => {
  const oversizedAtlas = installEnvironment({
    textureDimensions: [[4097, 1], [1, 1]],
  })
  await assert.rejects(
    createLive2DController(oversizedAtlas.canvas, {
      emotion: 'neutral',
      framing: 'full-body',
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
      state: 'idle',
    }),
    /atlas dimensions/,
  )
  assert.equal(oversizedAtlas.bitmapDecodeCount, 0)

  const excessiveAggregate = installEnvironment({
    textureDimensions: [[4096, 4096], [4096, 4096]],
  })
  await assert.rejects(
    createLive2DController(excessiveAggregate.canvas, {
      emotion: 'neutral',
      framing: 'full-body',
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
      state: 'idle',
    }),
    /decoded pixel budget/,
  )
  assert.equal(excessiveAggregate.bitmapDecodeCount, 0)
})

test('rejects physics3 metadata mismatches before starting animation', async () => {
  const invalidPhysics = makePhysicsDocument()
  invalidPhysics.Meta.TotalOutputCount = 2
  const environment = installEnvironment({ physicsDocument: invalidPhysics })

  await assert.rejects(
    createLive2DController(environment.canvas, {
      emotion: 'neutral',
      framing: 'full-body',
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
      state: 'idle',
    }),
    /physics totals do not match/,
  )
  assert.equal(environment.bitmapDecodeCount, 0)
})

test('fails before loading assets when WebGL2 is unavailable', async () => {
  const environment = installEnvironment({ webgl: false })
  await assert.rejects(
    createLive2DController(environment.canvas, {
      emotion: 'neutral',
      framing: 'half-body',
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
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
      expected: [0.961538, 0.641026, 0, 0],
      framing: 'full-body',
    },
  ]

  for (const { expected, framing } of cases) {
    const environment = installEnvironment()
    const controller = await createLive2DController(environment.canvas, {
      emotion: 'neutral',
      framing,
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
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
    modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
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
      modelManifestUrl: SAME_ORIGIN_MODEL_MANIFEST_URL,
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
