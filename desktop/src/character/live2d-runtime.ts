/**
 * Load the fixed local Elysia Cubism model through PurismCore and render it
 * with a small WebGL2 pipeline that has no third-party renderer dependency.
 */

import type { CharacterEmotion } from './character-emotion.ts'
import type { CharacterState } from './character-state.ts'

/** Closed camera profiles supported by the fixed Elysia Live2D model. */
export type Live2DFraming = 'full-body' | 'half-body' | 'call-half-body'

/** Initial semantic values required to start the Live2D renderer. */
export interface Live2DControllerOptions {
  readonly emotion: CharacterEmotion
  readonly framing: Live2DFraming
  readonly state: CharacterState
}

/** Imperative boundary owned by one mounted Live2D canvas. */
export interface Live2DController {
  /** Change the semantic activity pose without selecting an asset or motion. */
  setState(state: CharacterState): void
  /** Change the closed facial-emotion cue applied by the next animation frame. */
  setEmotion(emotion: CharacterEmotion): void
  /** Stop animation and release DOM, WebGL, image, and PurismCore resources. */
  dispose(): void
}

interface ModelFileReferences {
  readonly Moc?: unknown
  readonly Textures?: unknown
}

interface ModelManifest {
  readonly Version?: unknown
  readonly FileReferences?: ModelFileReferences
}

interface PurismParameters {
  readonly count: number
  readonly ids: readonly string[]
  readonly minimumValues: Float32Array
  readonly maximumValues: Float32Array
  readonly defaultValues: Float32Array
  readonly values: Float32Array
}

interface PurismDrawables {
  readonly count: number
  readonly constantFlags: Uint8Array
  readonly dynamicFlags: Uint8Array
  readonly textureIndices: Int32Array
  readonly opacities: Float32Array
  readonly maskCounts: Int32Array
  readonly vertexCounts: Int32Array
  readonly indexCounts: Int32Array
  readonly multiplyColors: Float32Array
  readonly screenColors: Float32Array
  readonly vertexPositions: readonly Float32Array[]
  readonly vertexUvs: readonly Float32Array[]
  readonly indices: readonly Uint16Array[]
  resetDynamicFlags(): void
}

interface PurismCanvasInfo {
  readonly CanvasWidth: number
  readonly CanvasHeight: number
  readonly CanvasOriginX: number
  readonly CanvasOriginY: number
  readonly PixelsPerUnit: number
}

interface PurismMoc {
  hasMocConsistency(bytes: ArrayBuffer): number | boolean | undefined
  _release?(): void
  release?(): void
}

interface PurismModel {
  readonly parameters: PurismParameters
  readonly drawables: PurismDrawables
  readonly canvasinfo: PurismCanvasInfo
  getDrawableRenderOrders?(): Int32Array
  getRenderOrders?(): Int32Array
  getLastError?(): number
  update(): void
  release(): void
}

interface PurismCoreNamespace {
  readonly Version: {
    csmGetVersion(): number
  }
  readonly Memory?: {
    initializeAmountOfMemory(bytes: number): void
  }
  readonly Moc: {
    fromArrayBuffer(bytes: ArrayBuffer): PurismMoc | null
  }
  readonly Model: {
    fromMoc(moc: PurismMoc): PurismModel | null
  }
}

interface Live2DWindow extends Window {
  PurismCore?: PurismCoreNamespace
}

interface AssetUrls {
  readonly manifest: URL
  readonly moc: URL
  readonly texture: URL
}

interface LoadedAssets {
  readonly manifest: ModelManifest
  readonly moc: Uint8Array
  readonly texture: Uint8Array
}

interface Live2DCameraViewport {
  readonly centerX: number
  readonly centerY: number
  readonly height: number
  readonly width: number
}

interface ShaderLocations {
  readonly position: number
  readonly uv: number
  readonly transform: WebGLUniformLocation
  readonly baseColor: WebGLUniformLocation
  readonly multiplyColor: WebGLUniformLocation
  readonly screenColor: WebGLUniformLocation
  readonly texture: WebGLUniformLocation
}

interface GpuResources {
  readonly program: WebGLProgram
  readonly vertexArray: WebGLVertexArrayObject
  readonly positionBuffer: WebGLBuffer
  readonly uvBuffer: WebGLBuffer
  readonly indexBuffer: WebGLBuffer
  readonly texture: WebGLTexture
  readonly locations: ShaderLocations
}

interface StatePose {
  readonly angleX: number
  readonly angleY: number
  readonly angleZ: number
  readonly bodyX: number
  readonly bodyY: number
  readonly bodyZ: number
  readonly browY: number
  readonly browForm: number
  readonly mouthForm: number
}

type DecodedTexture = ImageBitmap | HTMLImageElement

const CORE_SCRIPT_PATH = './character/live2d/runtime/purismcore.js'
const DEVELOPMENT_MODEL_BASE = './character/live2d/elysia/'
const PACKAGED_MODEL_BASE = 'elysia-asset://character/live2d/elysia/'
const MODEL_MANIFEST_NAME = 'model.model3.json'
const MODEL_MOC_NAME = 'model.moc3'
const MODEL_TEXTURE_NAME = 'textures/atlas.png'
const MODEL_MANIFEST_LIMIT = 64 * 1024
const MODEL_MOC_LIMIT = 8 * 1024 * 1024
const MODEL_TEXTURE_LIMIT = 4 * 1024 * 1024
const MODEL_TEXTURE_DIMENSION_LIMIT = 4096
const MODEL_TEXTURE_PIXEL_LIMIT = 16 * 1024 * 1024
const LOAD_TIMEOUT_MS = 10_000
const CORE_MEMORY_RESERVATION = 64 * 1024 * 1024
const MAX_DEVICE_PIXEL_RATIO = 2
const MAX_CANVAS_DIMENSION = 4096
const MAX_DRAWABLES = 512
const MAX_TOTAL_VERTICES = 1_000_000
const MAX_TOTAL_INDICES = 3_000_000
const RUNTIME_ERROR_EVENT = 'elysia:live2d-error'

const CHARACTER_STATES = new Set<CharacterState>([
  'idle',
  'listening',
  'thinking',
  'speaking',
  'working',
  'waiting_approval',
  'error',
])

const CHARACTER_EMOTIONS = new Set<CharacterEmotion>([
  'neutral',
  'happy',
  'sad',
])

const LIVE2D_FRAMINGS = new Set<Live2DFraming>([
  'full-body',
  'half-body',
  'call-half-body',
])

// These reviewed camera rectangles are deliberately pinned to this model's
// coordinates. Deriving a camera from animated vertices each frame would make
// breathing, hair, and arm motion visibly change the character's scale.
const CAMERA_BY_FRAMING: Readonly<Record<
  Live2DFraming,
  Live2DCameraViewport
>> = Object.freeze({
  'full-body': Object.freeze({
    centerX: 0,
    centerY: 0.01,
    width: 0.86,
    height: 2.02,
  }),
  'half-body': Object.freeze({
    centerX: 0,
    centerY: 0.57,
    width: 0.86,
    height: 0.84,
  }),
  'call-half-body': Object.freeze({
    centerX: 0,
    centerY: 0.43,
    width: 0.86,
    height: 1.16,
  }),
})

const REQUIRED_PARAMETERS = Object.freeze([
  'ParamEyeLOpen',
  'ParamEyeROpen',
  'ParamMouthOpenY',
  'ParamMouthForm',
  'ParamAngleX',
  'ParamAngleY',
  'ParamAngleZ',
  'ParamHairFront',
  'ParamHairBack',
  'ParamHairFrontV',
  'ParamHairBackV',
  'ParamBreath',
])

const MOUTH_OPEN_BY_DATASET: Readonly<Record<string, number>> = Object.freeze({
  closed: 0,
  small: 0.28,
  medium: 0.58,
  wide: 0.92,
})

const STATE_POSES: Readonly<Record<CharacterState, StatePose>> = Object.freeze({
  idle: Object.freeze({
    angleX: 0,
    angleY: 0,
    angleZ: 0,
    bodyX: 0,
    bodyY: 0,
    bodyZ: 0,
    browY: 0,
    browForm: 0,
    mouthForm: 0.08,
  }),
  listening: Object.freeze({
    angleX: 4,
    angleY: 1.5,
    angleZ: -1.5,
    bodyX: 1,
    bodyY: 0,
    bodyZ: -0.5,
    browY: 0.16,
    browForm: 0.08,
    mouthForm: 0.08,
  }),
  thinking: Object.freeze({
    angleX: 7,
    angleY: 3,
    angleZ: -2,
    bodyX: 1.5,
    bodyY: 0,
    bodyZ: -0.8,
    browY: 0.08,
    browForm: -0.08,
    mouthForm: -0.06,
  }),
  speaking: Object.freeze({
    angleX: 1.5,
    angleY: 0.5,
    angleZ: -0.5,
    bodyX: 0.8,
    bodyY: 0,
    bodyZ: -0.3,
    browY: 0.06,
    browForm: 0.08,
    mouthForm: 0.2,
  }),
  working: Object.freeze({
    angleX: -3,
    angleY: -3,
    angleZ: 1.2,
    bodyX: -1,
    bodyY: -0.5,
    bodyZ: 0.7,
    browY: -0.04,
    browForm: -0.08,
    mouthForm: 0,
  }),
  waiting_approval: Object.freeze({
    angleX: 0,
    angleY: 4,
    angleZ: 0,
    bodyX: 0,
    bodyY: 0.5,
    bodyZ: 0,
    browY: 0.18,
    browForm: 0.12,
    mouthForm: 0.05,
  }),
  error: Object.freeze({
    angleX: -2,
    angleY: -4,
    angleZ: 1.5,
    bodyX: -0.5,
    bodyY: -0.5,
    bodyZ: 0.8,
    browY: -0.18,
    browForm: -0.42,
    mouthForm: -0.48,
  }),
})

const VERTEX_SHADER_SOURCE = `#version 300 es
in vec2 a_position;
in vec2 a_uv;
uniform vec4 u_transform;
out vec2 v_uv;

void main() {
  gl_Position = vec4(
    a_position.x * u_transform.x + u_transform.z,
    a_position.y * u_transform.y + u_transform.w,
    0.0,
    1.0
  );
  v_uv = vec2(a_uv.x, 1.0 - a_uv.y);
}
`

const FRAGMENT_SHADER_SOURCE = `#version 300 es
precision mediump float;
in vec2 v_uv;
uniform sampler2D u_texture;
uniform vec4 u_base_color;
uniform vec4 u_multiply_color;
uniform vec4 u_screen_color;
out vec4 output_color;

void main() {
  vec4 texel = texture(u_texture, v_uv);
  texel.rgb *= u_multiply_color.rgb;
  texel.rgb = (texel.rgb + u_screen_color.rgb * texel.a)
    - (texel.rgb * u_screen_color.rgb);
  output_color = texel * u_base_color;
}
`

let purismCorePromise: Promise<PurismCoreNamespace> | undefined
let coreMemoryReserved = false

function asError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value))
}

function isFinitePositive(value: number): boolean {
  return Number.isFinite(value) && value > 0
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

function smoothStep(value: number): number {
  const bounded = clamp(value, 0, 1)
  return bounded * bounded * (3 - 2 * bounded)
}

function resolveAssetUrls(): AssetUrls {
  const pageProtocol = window.location.protocol
  let base: URL
  if (pageProtocol === 'file:') {
    base = new URL(PACKAGED_MODEL_BASE)
  } else if (pageProtocol === 'http:' || pageProtocol === 'https:') {
    base = new URL(DEVELOPMENT_MODEL_BASE, document.baseURI)
    if (base.origin !== window.location.origin) {
      throw new Error('Live2D development assets must remain same-origin.')
    }
  } else {
    throw new Error(`Unsupported Live2D page protocol: ${pageProtocol}`)
  }

  const manifest = new URL(MODEL_MANIFEST_NAME, base)
  const moc = new URL(MODEL_MOC_NAME, base)
  const texture = new URL(MODEL_TEXTURE_NAME, base)
  for (const url of [manifest, moc, texture]) {
    if (url.username !== '' || url.password !== '' || url.search !== '' || url.hash !== '') {
      throw new Error('Live2D asset URLs cannot contain credentials or URL modifiers.')
    }
  }
  return { manifest, moc, texture }
}

function validateManifest(value: unknown): ModelManifest {
  if (typeof value !== 'object' || value === null) {
    throw new Error('The Live2D model manifest must be a JSON object.')
  }
  const manifest = value as ModelManifest
  const references = manifest.FileReferences
  if (
    manifest.Version !== 3
    || typeof references !== 'object'
    || references === null
    || references.Moc !== MODEL_MOC_NAME
    || !Array.isArray(references.Textures)
    || references.Textures.length !== 1
    || references.Textures[0] !== MODEL_TEXTURE_NAME
  ) {
    // Resource references are data in Cubism manifests. Exact comparison keeps
    // a replaced manifest from turning this local renderer into a URL loader.
    throw new Error('The Live2D model manifest does not match the fixed asset contract.')
  }
  return manifest
}

function validateResponseUrl(response: Response, expected: URL): void {
  if (response.redirected) {
    throw new Error(`Redirected Live2D asset rejected: ${expected.pathname}`)
  }
  if (response.url !== '' && response.url !== expected.href) {
    throw new Error(`Unexpected Live2D response URL: ${response.url}`)
  }
}

async function readBoundedResponse(
  response: Response,
  maximumBytes: number,
): Promise<Uint8Array> {
  const declaredLength = response.headers.get('content-length')
  if (declaredLength !== null) {
    const parsedLength = Number(declaredLength)
    if (!Number.isSafeInteger(parsedLength) || parsedLength < 0 || parsedLength > maximumBytes) {
      throw new Error('Live2D asset exceeds its declared size limit.')
    }
  }
  if (response.body === null) {
    throw new Error('Live2D asset response has no readable body.')
  }

  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let received = 0
  try {
    while (true) {
      const result = await reader.read()
      if (result.done) {
        break
      }
      received += result.value.byteLength
      if (received > maximumBytes) {
        // Cancel immediately so a malicious or damaged local response cannot
        // continue consuming renderer memory after crossing the hard limit.
        await reader.cancel('Live2D asset size limit exceeded.')
        throw new Error('Live2D asset exceeds its streamed size limit.')
      }
      chunks.push(result.value)
    }
  } finally {
    reader.releaseLock()
  }

  const bytes = new Uint8Array(received)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  return bytes
}

async function fetchFixedAsset(
  url: URL,
  maximumBytes: number,
  signal: AbortSignal,
): Promise<Uint8Array> {
  const response = await fetch(url, {
    cache: 'force-cache',
    credentials: 'omit',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
    signal,
  })
  if (!response.ok) {
    throw new Error(`Live2D asset request failed (${response.status}): ${url.pathname}`)
  }
  validateResponseUrl(response, url)
  return readBoundedResponse(response, maximumBytes)
}

async function loadFixedAssets(): Promise<LoadedAssets> {
  const urls = resolveAssetUrls()
  const abortController = new AbortController()
  const timeout = globalThis.setTimeout(() => {
    abortController.abort(new Error('Live2D asset loading timed out.'))
  }, LOAD_TIMEOUT_MS)

  try {
    const [manifestBytes, moc, texture] = await Promise.all([
      fetchFixedAsset(urls.manifest, MODEL_MANIFEST_LIMIT, abortController.signal),
      fetchFixedAsset(urls.moc, MODEL_MOC_LIMIT, abortController.signal),
      fetchFixedAsset(urls.texture, MODEL_TEXTURE_LIMIT, abortController.signal),
    ])
    let parsedManifest: unknown
    try {
      parsedManifest = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(manifestBytes))
    } catch (error) {
      throw new Error('The Live2D model manifest is not valid UTF-8 JSON.', {
        cause: error,
      })
    }
    if (
      moc.byteLength < 4
      || moc[0] !== 0x4d
      || moc[1] !== 0x4f
      || moc[2] !== 0x43
      || moc[3] !== 0x33
    ) {
      throw new Error('The fixed Live2D moc does not have a MOC3 header.')
    }
    if (
      texture.byteLength < 8
      || texture[0] !== 0x89
      || texture[1] !== 0x50
      || texture[2] !== 0x4e
      || texture[3] !== 0x47
      || texture[4] !== 0x0d
      || texture[5] !== 0x0a
      || texture[6] !== 0x1a
      || texture[7] !== 0x0a
    ) {
      throw new Error('The fixed Live2D texture does not have a PNG signature.')
    }
    return {
      manifest: validateManifest(parsedManifest),
      moc,
      texture,
    }
  } catch (error) {
    const timedOut = abortController.signal.aborted
    if (!timedOut) {
      // Promise.all stops observing siblings after the first rejection. Abort
      // their streams explicitly so a damaged local asset cannot leave the
      // other two requests consuming memory until their individual reads end.
      abortController.abort(error)
    }
    if (timedOut) {
      throw new Error('Live2D asset loading timed out.', { cause: error })
    }
    throw error
  } finally {
    globalThis.clearTimeout(timeout)
  }
}

function getWindowPurismCore(): PurismCoreNamespace | undefined {
  return (window as Live2DWindow).PurismCore
}

async function waitForPurismCore(): Promise<PurismCoreNamespace> {
  const startedAt = Date.now()
  while (Date.now() - startedAt < LOAD_TIMEOUT_MS) {
    const core = getWindowPurismCore()
    if (core !== undefined) {
      try {
        const version = core.Version.csmGetVersion()
        if (Number.isInteger(version) && version > 0) {
          return core
        }
      } catch {
        // Purism's single-file Emscripten bootstrap finishes asynchronously
        // after the script load event; polling avoids racing its WASM exports.
      }
    }
    await new Promise<void>((resolve) => {
      globalThis.setTimeout(resolve, 16)
    })
  }
  throw new Error('PurismCore did not initialize before the timeout.')
}

function loadPurismCore(): Promise<PurismCoreNamespace> {
  if (purismCorePromise !== undefined) {
    return purismCorePromise
  }

  purismCorePromise = (async () => {
    if (getWindowPurismCore() === undefined) {
      const source = new URL(CORE_SCRIPT_PATH, document.baseURI)
      if (
        window.location.protocol !== 'file:'
        && source.origin !== window.location.origin
      ) {
        throw new Error('PurismCore must load from the application origin.')
      }
      await new Promise<void>((resolve, reject) => {
        const script = document.createElement('script')
        script.async = true
        script.src = source.href
        script.dataset.elysiaLive2dCore = 'true'
        script.addEventListener('load', () => { resolve() }, { once: true })
        script.addEventListener('error', () => {
          script.remove()
          reject(new Error('Failed to load the local PurismCore runtime.'))
        }, { once: true })
        document.head.append(script)
      })
    }
    const core = await waitForPurismCore()
    if (!coreMemoryReserved) {
      // Purism views point directly into WASM memory. Reserving before any moc
      // prevents a later model allocation from growing memory and detaching an
      // already-running controller's typed-array views.
      core.Memory?.initializeAmountOfMemory(CORE_MEMORY_RESERVATION)
      coreMemoryReserved = true
    }
    return core
  })().catch((error: unknown) => {
    purismCorePromise = undefined
    throw error
  })
  return purismCorePromise
}

function exactArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.slice().buffer
}

function releaseMoc(moc: PurismMoc): void {
  if (typeof moc.release === 'function') {
    moc.release()
  } else {
    moc._release?.()
  }
}

function createCoreModel(
  core: PurismCoreNamespace,
  mocBytes: Uint8Array,
): { moc: PurismMoc; model: PurismModel } {
  const buffer = exactArrayBuffer(mocBytes)
  const moc = core.Moc.fromArrayBuffer(buffer)
  if (moc === null) {
    throw new Error('PurismCore rejected the fixed Live2D moc.')
  }
  try {
    const consistency = moc.hasMocConsistency(buffer)
    if (consistency !== 1 && consistency !== true) {
      throw new Error('The fixed Live2D moc failed its consistency check.')
    }

    // Consistency checking allocates temporary WASM memory. Constructing the
    // model afterwards is essential because its public arrays must be created
    // only after that possible memory growth has finished.
    const model = core.Model.fromMoc(moc)
    if (model === null) {
      throw new Error('PurismCore could not instantiate the fixed Live2D model.')
    }
    return { moc, model }
  } catch (error) {
    releaseMoc(moc)
    throw error
  }
}

function getRenderOrders(model: PurismModel): Int32Array {
  const orders = model.getDrawableRenderOrders?.() ?? model.getRenderOrders?.()
  if (orders === undefined || orders.length !== model.drawables.count) {
    throw new Error('PurismCore returned an invalid Live2D render-order table.')
  }
  return orders
}

function validateCoreModel(model: PurismModel): ReadonlyMap<string, number> {
  const parameters = model.parameters
  if (
    parameters.count <= 0
    || parameters.ids.length !== parameters.count
    || parameters.minimumValues.length !== parameters.count
    || parameters.maximumValues.length !== parameters.count
    || parameters.defaultValues.length !== parameters.count
    || parameters.values.length !== parameters.count
  ) {
    throw new Error('PurismCore returned an invalid Live2D parameter table.')
  }
  const parameterIndices = new Map<string, number>()
  parameters.ids.forEach((id, index) => {
    const minimum = parameters.minimumValues[index]
    const maximum = parameters.maximumValues[index]
    const defaultValue = parameters.defaultValues[index]
    const currentValue = parameters.values[index]
    if (
      typeof id !== 'string'
      || id.length === 0
      || !Number.isFinite(minimum)
      || !Number.isFinite(maximum)
      || !Number.isFinite(defaultValue)
      || !Number.isFinite(currentValue)
      || minimum > maximum
      || defaultValue < minimum
      || defaultValue > maximum
      || currentValue < minimum
      || currentValue > maximum
    ) {
      // Every interpolated value starts from the Core table's current value.
      // Reject malformed bounds up front; clamping only the target cannot
      // recover safely from NaN, reversed limits, or an out-of-range origin.
      throw new Error(`Live2D parameter ${index} has invalid bounds or values.`)
    }
    if (parameterIndices.has(id)) {
      throw new Error(`Duplicate Live2D parameter id: ${id}`)
    }
    parameterIndices.set(id, index)
  })
  for (const required of REQUIRED_PARAMETERS) {
    if (!parameterIndices.has(required)) {
      throw new Error(`The Live2D model is missing required parameter ${required}.`)
    }
  }

  const drawables = model.drawables
  if (drawables.count <= 0 || drawables.count > MAX_DRAWABLES) {
    throw new Error('The Live2D drawable count is outside its fixed limit.')
  }
  const parallelLengths = [
    drawables.constantFlags.length,
    drawables.dynamicFlags.length,
    drawables.textureIndices.length,
    drawables.opacities.length,
    drawables.maskCounts.length,
    drawables.vertexCounts.length,
    drawables.indexCounts.length,
    drawables.vertexPositions.length,
    drawables.vertexUvs.length,
    drawables.indices.length,
  ]
  if (parallelLengths.some((length) => length !== drawables.count)) {
    throw new Error('PurismCore returned mismatched Live2D drawable arrays.')
  }
  if (
    drawables.multiplyColors.length !== drawables.count * 4
    || drawables.screenColors.length !== drawables.count * 4
  ) {
    throw new Error('PurismCore returned mismatched Live2D color arrays.')
  }
  if (Array.from(drawables.maskCounts).some((count) => count !== 0)) {
    // The reviewed v2 model deliberately has no clipping masks. Failing closed
    // is safer than silently drawing a future masked model incorrectly.
    throw new Error('This Live2D renderer accepts only the reviewed mask-free model.')
  }
  if (Array.from(drawables.textureIndices).some((index) => index !== 0)) {
    throw new Error('This Live2D renderer accepts only the fixed single atlas.')
  }

  let totalVertices = 0
  let totalIndices = 0
  for (let index = 0; index < drawables.count; index += 1) {
    const vertexCount = drawables.vertexCounts[index] ?? -1
    const indexCount = drawables.indexCounts[index] ?? -1
    if (
      vertexCount < 0
      || indexCount < 0
      || drawables.vertexPositions[index]?.length !== vertexCount * 2
      || drawables.vertexUvs[index]?.length !== vertexCount * 2
      || drawables.indices[index]?.length !== indexCount
      || drawables.indices[index]?.some(
        (vertexIndex) => vertexIndex >= vertexCount,
      ) === true
    ) {
      throw new Error(`Live2D drawable ${index} has invalid mesh arrays.`)
    }
    totalVertices += vertexCount
    totalIndices += indexCount
  }
  if (totalVertices > MAX_TOTAL_VERTICES || totalIndices > MAX_TOTAL_INDICES) {
    throw new Error('The Live2D mesh exceeds its fixed geometry limits.')
  }
  getRenderOrders(model)

  const canvas = model.canvasinfo
  if (
    !isFinitePositive(canvas.CanvasWidth)
    || !isFinitePositive(canvas.CanvasHeight)
    || !isFinitePositive(canvas.PixelsPerUnit)
    || !Number.isFinite(canvas.CanvasOriginX)
    || !Number.isFinite(canvas.CanvasOriginY)
  ) {
    throw new Error('PurismCore returned invalid Live2D canvas metadata.')
  }
  return parameterIndices
}

async function decodeTexture(bytes: Uint8Array): Promise<DecodedTexture> {
  const blob = new Blob([exactArrayBuffer(bytes)], { type: 'image/png' })
  if (typeof globalThis.createImageBitmap === 'function') {
    return globalThis.createImageBitmap(blob, {
      colorSpaceConversion: 'default',
      imageOrientation: 'none',
      premultiplyAlpha: 'premultiply',
    })
  }

  const objectUrl = URL.createObjectURL(blob)
  try {
    const image = new Image()
    image.decoding = 'async'
    image.src = objectUrl
    await image.decode()
    return image
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

function closeDecodedTexture(texture: DecodedTexture): void {
  if ('close' in texture && typeof texture.close === 'function') {
    texture.close()
  }
}

function compileShader(
  gl: WebGL2RenderingContext,
  type: number,
  source: string,
): WebGLShader {
  const shader = gl.createShader(type)
  if (shader === null) {
    throw new Error('WebGL2 could not allocate a Live2D shader.')
  }
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const detail = gl.getShaderInfoLog(shader) ?? 'unknown shader error'
    gl.deleteShader(shader)
    throw new Error(`Live2D shader compilation failed: ${detail}`)
  }
  return shader
}

function requireUniform(
  gl: WebGL2RenderingContext,
  program: WebGLProgram,
  name: string,
): WebGLUniformLocation {
  const location = gl.getUniformLocation(program, name)
  if (location === null) {
    throw new Error(`Live2D shader uniform is unavailable: ${name}`)
  }
  return location
}

function createGpuResources(
  gl: WebGL2RenderingContext,
  decodedTexture: DecodedTexture,
): GpuResources {
  if (
    decodedTexture.width <= 0
    || decodedTexture.height <= 0
    || decodedTexture.width > MODEL_TEXTURE_DIMENSION_LIMIT
    || decodedTexture.height > MODEL_TEXTURE_DIMENSION_LIMIT
    || decodedTexture.width * decodedTexture.height > MODEL_TEXTURE_PIXEL_LIMIT
  ) {
    throw new Error('The Live2D atlas dimensions exceed their fixed limit.')
  }

  const vertexShader = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER_SOURCE)
  let fragmentShader: WebGLShader
  try {
    fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER_SOURCE)
  } catch (error) {
    gl.deleteShader(vertexShader)
    throw error
  }
  const program = gl.createProgram()
  if (program === null) {
    gl.deleteShader(vertexShader)
    gl.deleteShader(fragmentShader)
    throw new Error('WebGL2 could not allocate the Live2D shader program.')
  }
  try {
    gl.attachShader(program, vertexShader)
    gl.attachShader(program, fragmentShader)
    gl.linkProgram(program)
  } catch (error) {
    gl.deleteProgram(program)
    throw error
  } finally {
    gl.deleteShader(vertexShader)
    gl.deleteShader(fragmentShader)
  }
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const detail = gl.getProgramInfoLog(program) ?? 'unknown link error'
    gl.deleteProgram(program)
    throw new Error(`Live2D shader linking failed: ${detail}`)
  }

  const vertexArray = gl.createVertexArray()
  const positionBuffer = gl.createBuffer()
  const uvBuffer = gl.createBuffer()
  const indexBuffer = gl.createBuffer()
  const texture = gl.createTexture()
  if (
    vertexArray === null
    || positionBuffer === null
    || uvBuffer === null
    || indexBuffer === null
    || texture === null
  ) {
    gl.deleteProgram(program)
    if (vertexArray !== null) gl.deleteVertexArray(vertexArray)
    if (positionBuffer !== null) gl.deleteBuffer(positionBuffer)
    if (uvBuffer !== null) gl.deleteBuffer(uvBuffer)
    if (indexBuffer !== null) gl.deleteBuffer(indexBuffer)
    if (texture !== null) gl.deleteTexture(texture)
    throw new Error('WebGL2 could not allocate Live2D drawing resources.')
  }

  const releaseAllocations = (): void => {
    gl.deleteProgram(program)
    gl.deleteVertexArray(vertexArray)
    gl.deleteBuffer(positionBuffer)
    gl.deleteBuffer(uvBuffer)
    gl.deleteBuffer(indexBuffer)
    gl.deleteTexture(texture)
  }
  try {
    const position = gl.getAttribLocation(program, 'a_position')
    const uv = gl.getAttribLocation(program, 'a_uv')
    if (position < 0 || uv < 0) {
      throw new Error('Live2D shader attributes are unavailable.')
    }
    const locations: ShaderLocations = {
      position,
      uv,
      transform: requireUniform(gl, program, 'u_transform'),
      baseColor: requireUniform(gl, program, 'u_base_color'),
      multiplyColor: requireUniform(gl, program, 'u_multiply_color'),
      screenColor: requireUniform(gl, program, 'u_screen_color'),
      texture: requireUniform(gl, program, 'u_texture'),
    }

    gl.bindVertexArray(vertexArray)
    gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer)
    gl.enableVertexAttribArray(position)
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0)
    gl.bindBuffer(gl.ARRAY_BUFFER, uvBuffer)
    gl.enableVertexAttribArray(uv)
    gl.vertexAttribPointer(uv, 2, gl.FLOAT, false, 0, 0)
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer)

    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.RGBA,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      decodedTexture,
    )
    gl.bindVertexArray(null)
    gl.bindTexture(gl.TEXTURE_2D, null)

    return {
      program,
      vertexArray,
      positionBuffer,
      uvBuffer,
      indexBuffer,
      texture,
      locations,
    }
  } catch (error) {
    releaseAllocations()
    throw error
  }
}

function deleteGpuResources(
  gl: WebGL2RenderingContext,
  resources: GpuResources,
): void {
  gl.deleteTexture(resources.texture)
  gl.deleteBuffer(resources.indexBuffer)
  gl.deleteBuffer(resources.uvBuffer)
  gl.deleteBuffer(resources.positionBuffer)
  gl.deleteVertexArray(resources.vertexArray)
  gl.deleteProgram(resources.program)
}

function validateState(state: CharacterState): void {
  if (!CHARACTER_STATES.has(state)) {
    throw new TypeError(`Unsupported Live2D character state: ${String(state)}`)
  }
}

function validateEmotion(emotion: CharacterEmotion): void {
  if (!CHARACTER_EMOTIONS.has(emotion)) {
    throw new TypeError(`Unsupported Live2D character emotion: ${String(emotion)}`)
  }
}

function validateFraming(framing: Live2DFraming): void {
  if (!LIVE2D_FRAMINGS.has(framing)) {
    throw new TypeError(`Unsupported Live2D character framing: ${String(framing)}`)
  }
}

class Live2DControllerImplementation implements Live2DController {
  readonly #canvas: HTMLCanvasElement
  readonly #gl: WebGL2RenderingContext
  readonly #model: PurismModel
  readonly #moc: PurismMoc
  readonly #parameterIndices: ReadonlyMap<string, number>
  readonly #textureBytes: Uint8Array
  readonly #framing: Live2DFraming
  #gpu: GpuResources | null = null
  #state: CharacterState
  #emotion: CharacterEmotion
  #frameRequest: number | null = null
  #resizeObserver: ResizeObserver | null = null
  #startedAt = performance.now()
  #lastFrameAt = this.#startedAt
  #disposed = false
  #contextLost = false
  #restorationRevision = 0

  constructor(
    canvas: HTMLCanvasElement,
    gl: WebGL2RenderingContext,
    model: PurismModel,
    moc: PurismMoc,
    parameterIndices: ReadonlyMap<string, number>,
    textureBytes: Uint8Array,
    options: Live2DControllerOptions,
  ) {
    this.#canvas = canvas
    this.#gl = gl
    this.#model = model
    this.#moc = moc
    this.#parameterIndices = parameterIndices
    this.#textureBytes = textureBytes
    this.#framing = options.framing
    this.#state = options.state
    this.#emotion = options.emotion
  }

  async initialize(): Promise<void> {
    const decodedTexture = await decodeTexture(this.#textureBytes)
    try {
      this.#gpu = createGpuResources(this.#gl, decodedTexture)
    } finally {
      closeDecodedTexture(decodedTexture)
    }

    this.#canvas.addEventListener('webglcontextlost', this.#handleContextLost)
    this.#canvas.addEventListener('webglcontextrestored', this.#handleContextRestored)
    if (typeof globalThis.ResizeObserver === 'function') {
      this.#resizeObserver = new ResizeObserver(() => { this.#resizeCanvas() })
      this.#resizeObserver.observe(this.#canvas)
    } else {
      window.addEventListener('resize', this.#resizeCanvas)
    }
    this.#resizeCanvas()
    this.#canvas.dataset.live2dStatus = 'ready'
    this.#frameRequest = window.requestAnimationFrame(this.#renderFrame)
  }

  setState(state: CharacterState): void {
    validateState(state)
    if (!this.#disposed) {
      this.#state = state
    }
  }

  setEmotion(emotion: CharacterEmotion): void {
    validateEmotion(emotion)
    if (!this.#disposed) {
      this.#emotion = emotion
    }
  }

  dispose(): void {
    if (this.#disposed) {
      return
    }
    this.#disposed = true
    this.#restorationRevision += 1
    if (this.#frameRequest !== null) {
      window.cancelAnimationFrame(this.#frameRequest)
      this.#frameRequest = null
    }
    this.#resizeObserver?.disconnect()
    this.#resizeObserver = null
    window.removeEventListener('resize', this.#resizeCanvas)
    this.#canvas.removeEventListener('webglcontextlost', this.#handleContextLost)
    this.#canvas.removeEventListener('webglcontextrestored', this.#handleContextRestored)
    try {
      if (this.#gpu !== null && !this.#contextLost) {
        deleteGpuResources(this.#gl, this.#gpu)
      }
    } finally {
      this.#gpu = null
      try {
        this.#model.release()
      } finally {
        try {
          releaseMoc(this.#moc)
        } finally {
          delete this.#canvas.dataset.live2dStatus
        }
      }
    }
  }

  readonly #resizeCanvas = (): void => {
    if (this.#disposed) {
      return
    }
    const bounds = this.#canvas.getBoundingClientRect()
    const ratio = clamp(window.devicePixelRatio || 1, 1, MAX_DEVICE_PIXEL_RATIO)
    const width = clamp(Math.round(bounds.width * ratio), 1, MAX_CANVAS_DIMENSION)
    const height = clamp(Math.round(bounds.height * ratio), 1, MAX_CANVAS_DIMENSION)
    if (this.#canvas.width !== width || this.#canvas.height !== height) {
      this.#canvas.width = width
      this.#canvas.height = height
    }
  }

  readonly #handleContextLost = (event: Event): void => {
    event.preventDefault()
    // A second loss can occur while texture decoding for the preceding restore
    // is still pending. Invalidate that task so it cannot mark a lost context
    // ready or restart animation after its obsolete upload completes.
    this.#restorationRevision += 1
    this.#contextLost = true
    this.#gpu = null
    if (this.#frameRequest !== null) {
      window.cancelAnimationFrame(this.#frameRequest)
      this.#frameRequest = null
    }
    if (!this.#disposed) {
      this.#canvas.dataset.live2dStatus = 'recovering'
    }
  }

  readonly #handleContextRestored = (): void => {
    if (this.#disposed) {
      return
    }
    const revision = ++this.#restorationRevision
    void this.#restoreAfterContextLoss(revision)
  }

  async #restoreAfterContextLoss(revision: number): Promise<void> {
    try {
      const decodedTexture = await decodeTexture(this.#textureBytes)
      let gpu: GpuResources
      try {
        gpu = createGpuResources(this.#gl, decodedTexture)
      } finally {
        closeDecodedTexture(decodedTexture)
      }
      if (this.#disposed || revision !== this.#restorationRevision) {
        deleteGpuResources(this.#gl, gpu)
        return
      }
      this.#gpu = gpu
      this.#contextLost = false
      this.#lastFrameAt = performance.now()
      this.#canvas.dataset.live2dStatus = 'ready'
      this.#frameRequest = window.requestAnimationFrame(this.#renderFrame)
    } catch (error) {
      if (!this.#disposed && revision === this.#restorationRevision) {
        this.#reportRuntimeFailure(asError(error))
      }
    }
  }

  #setParameter(id: string, target: number, blend: number): void {
    const index = this.#parameterIndices.get(id)
    if (index === undefined) {
      return
    }
    const parameters = this.#model.parameters
    const bounded = clamp(
      target,
      parameters.minimumValues[index] ?? target,
      parameters.maximumValues[index] ?? target,
    )
    const current = parameters.values[index] ?? bounded
    parameters.values[index] = current + (bounded - current) * blend
  }

  #applyAnimation(elapsedSeconds: number, deltaSeconds: number): void {
    const pose = STATE_POSES[this.#state]
    const blend = 1 - Math.exp(-deltaSeconds * 9)
    const slowHead = Math.sin(elapsedSeconds * 0.72)
    const secondaryHead = Math.sin(elapsedSeconds * 0.47 + 0.8)
    const angleX = pose.angleX + slowHead * 1.4
    const angleY = pose.angleY + secondaryHead * 0.85
    const angleZ = pose.angleZ + Math.sin(elapsedSeconds * 0.39 + 1.7) * 0.55

    this.#setParameter('ParamAngleX', angleX, blend)
    this.#setParameter('ParamAngleY', angleY, blend)
    this.#setParameter('ParamAngleZ', angleZ, blend)
    this.#setParameter('ParamBodyAngleX', pose.bodyX + slowHead * 0.35, blend)
    this.#setParameter('ParamBodyAngleY', pose.bodyY, blend)
    this.#setParameter('ParamBodyAngleZ', pose.bodyZ - slowHead * 0.28, blend)
    this.#setParameter('ParamBrowLY', pose.browY, blend)
    this.#setParameter('ParamBrowRY', pose.browY, blend)
    this.#setParameter('ParamBrowLForm', pose.browForm, blend)
    this.#setParameter('ParamBrowRForm', pose.browForm, blend)

    let mouthForm = pose.mouthForm
    let eyeSmile = 0
    let cheek = 0
    if (this.#emotion === 'happy') {
      mouthForm += 0.5
      eyeSmile = 0.55
      cheek = 0.08
    } else if (this.#emotion === 'sad') {
      mouthForm -= 0.45
      this.#setParameter('ParamBrowLY', -0.12, blend)
      this.#setParameter('ParamBrowRY', -0.12, blend)
      this.#setParameter('ParamBrowLForm', -0.32, blend)
      this.#setParameter('ParamBrowRForm', -0.32, blend)
    }
    this.#setParameter('ParamMouthForm', mouthForm, blend)
    this.#setParameter('ParamEyeLSmile', eyeSmile, blend)
    this.#setParameter('ParamEyeRSmile', eyeSmile, blend)
    this.#setParameter('ParamCheek', cheek, blend)

    const breath = 0.5 + Math.sin(elapsedSeconds * 1.65) * 0.5
    this.#setParameter('ParamBreath', breath, blend)
    this.#setParameter(
      'ParamHairFront',
      Math.sin(elapsedSeconds * 1.18) * 0.13 + angleX / 250,
      blend,
    )
    this.#setParameter(
      'ParamHairBack',
      Math.sin(elapsedSeconds * 0.93 + 0.9) * 0.12 - angleX / 280,
      blend,
    )
    this.#setParameter('ParamHairFrontV', Math.sin(elapsedSeconds * 1.42 + 0.3) * 0.08, blend)
    this.#setParameter('ParamHairBackV', Math.sin(elapsedSeconds * 1.06 + 1.4) * 0.09, blend)

    // A deterministic 4.7 second cycle gives a short close-and-open blink
    // without timers or random state that could outlive a disposed controller.
    const blinkPhase = elapsedSeconds % 4.7
    let eyeOpen = 1
    if (blinkPhase < 0.11) {
      eyeOpen = 1 - smoothStep(blinkPhase / 0.11)
    } else if (blinkPhase < 0.19) {
      eyeOpen = smoothStep((blinkPhase - 0.11) / 0.08)
    }
    this.#setParameter('ParamEyeLOpen', eyeOpen, 1)
    this.#setParameter('ParamEyeROpen', eyeOpen, 1)

    const mouthDataset = document.documentElement.dataset.characterMouth ?? 'closed'
    const mouthOpen = this.#state === 'speaking'
      ? (MOUTH_OPEN_BY_DATASET[mouthDataset] ?? 0)
      : 0
    this.#setParameter('ParamMouthOpenY', mouthOpen, 1)
  }

  #resizeAndGetTransform(): readonly [number, number, number, number] {
    this.#resizeCanvas()
    const camera = CAMERA_BY_FRAMING[this.#framing]
    const pixelsPerModelUnit = Math.min(
      this.#canvas.width / camera.width,
      this.#canvas.height / camera.height,
    )
    const scaleX = 2 * pixelsPerModelUnit / this.#canvas.width
    const scaleY = 2 * pixelsPerModelUnit / this.#canvas.height
    return [
      scaleX,
      scaleY,
      -camera.centerX * scaleX,
      -camera.centerY * scaleY,
    ]
  }

  #configureBlendMode(constantFlags: number): void {
    const gl = this.#gl
    if ((constantFlags & 1) !== 0) {
      gl.blendFuncSeparate(gl.ONE, gl.ONE, gl.ZERO, gl.ONE)
    } else if ((constantFlags & 2) !== 0) {
      gl.blendFuncSeparate(gl.DST_COLOR, gl.ONE_MINUS_SRC_ALPHA, gl.ZERO, gl.ONE)
    } else {
      gl.blendFuncSeparate(
        gl.ONE,
        gl.ONE_MINUS_SRC_ALPHA,
        gl.ONE,
        gl.ONE_MINUS_SRC_ALPHA,
      )
    }
  }

  #drawModel(resources: GpuResources): void {
    const gl = this.#gl
    const drawables = this.#model.drawables
    const transform = this.#resizeAndGetTransform()

    gl.viewport(0, 0, this.#canvas.width, this.#canvas.height)
    gl.disable(gl.DEPTH_TEST)
    gl.disable(gl.STENCIL_TEST)
    gl.disable(gl.SCISSOR_TEST)
    gl.enable(gl.BLEND)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
    gl.useProgram(resources.program)
    gl.bindVertexArray(resources.vertexArray)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, resources.texture)
    gl.uniform1i(resources.locations.texture, 0)
    gl.uniform4f(resources.locations.transform, ...transform)

    // Render order is a mutable value per drawable, not an index array. A full
    // stable sort and complete upload every frame handles parameter-driven
    // order and mesh changes without stale GPU caches.
    const renderOrders = getRenderOrders(this.#model)
    const sortedDrawables = Array.from(
      { length: drawables.count },
      (_, index) => index,
    ).sort((left, right) => (
      (renderOrders[left] ?? left) - (renderOrders[right] ?? right)
      || left - right
    ))

    for (const drawableIndex of sortedDrawables) {
      const visible = ((drawables.dynamicFlags[drawableIndex] ?? 0) & 1) !== 0
      const opacity = drawables.opacities[drawableIndex] ?? 0
      const indexCount = drawables.indexCounts[drawableIndex] ?? 0
      if (!visible || opacity <= 0 || indexCount <= 0) {
        continue
      }

      const constantFlags = drawables.constantFlags[drawableIndex] ?? 0
      if ((constantFlags & 4) !== 0) {
        gl.disable(gl.CULL_FACE)
      } else {
        gl.enable(gl.CULL_FACE)
        gl.frontFace(gl.CCW)
        gl.cullFace(gl.BACK)
      }
      this.#configureBlendMode(constantFlags)

      gl.bindBuffer(gl.ARRAY_BUFFER, resources.positionBuffer)
      gl.bufferData(
        gl.ARRAY_BUFFER,
        drawables.vertexPositions[drawableIndex] ?? new Float32Array(0),
        gl.STREAM_DRAW,
      )
      gl.bindBuffer(gl.ARRAY_BUFFER, resources.uvBuffer)
      gl.bufferData(
        gl.ARRAY_BUFFER,
        drawables.vertexUvs[drawableIndex] ?? new Float32Array(0),
        gl.STREAM_DRAW,
      )
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, resources.indexBuffer)
      gl.bufferData(
        gl.ELEMENT_ARRAY_BUFFER,
        drawables.indices[drawableIndex] ?? new Uint16Array(0),
        gl.STREAM_DRAW,
      )

      const colorOffset = drawableIndex * 4
      gl.uniform4f(resources.locations.baseColor, 1, 1, 1, opacity)
      gl.uniform4f(
        resources.locations.multiplyColor,
        drawables.multiplyColors[colorOffset] ?? 1,
        drawables.multiplyColors[colorOffset + 1] ?? 1,
        drawables.multiplyColors[colorOffset + 2] ?? 1,
        drawables.multiplyColors[colorOffset + 3] ?? 1,
      )
      gl.uniform4f(
        resources.locations.screenColor,
        drawables.screenColors[colorOffset] ?? 0,
        drawables.screenColors[colorOffset + 1] ?? 0,
        drawables.screenColors[colorOffset + 2] ?? 0,
        drawables.screenColors[colorOffset + 3] ?? 0,
      )
      gl.drawElements(gl.TRIANGLES, indexCount, gl.UNSIGNED_SHORT, 0)
    }

    gl.bindVertexArray(null)
    gl.bindTexture(gl.TEXTURE_2D, null)
    drawables.resetDynamicFlags()
  }

  readonly #renderFrame = (timestamp: number): void => {
    this.#frameRequest = null
    if (this.#disposed || this.#contextLost || this.#gpu === null) {
      return
    }
    try {
      const deltaSeconds = clamp((timestamp - this.#lastFrameAt) / 1000, 1 / 240, 0.1)
      const elapsedSeconds = (timestamp - this.#startedAt) / 1000
      this.#lastFrameAt = timestamp
      this.#applyAnimation(elapsedSeconds, deltaSeconds)
      this.#model.update()
      if ((this.#model.getLastError?.() ?? 0) !== 0) {
        throw new Error('PurismCore reported an error while updating the Live2D model.')
      }
      this.#drawModel(this.#gpu)
      this.#frameRequest = window.requestAnimationFrame(this.#renderFrame)
    } catch (error) {
      this.#reportRuntimeFailure(asError(error))
    }
  }

  #reportRuntimeFailure(error: Error): void {
    if (this.#disposed) {
      return
    }
    // The controller owns Core and GPU allocations, so it must not depend on a
    // React or desktop-pet listener to release them after a fatal frame error.
    // Dispatch remains the presentation boundary that selects static fallback.
    try {
      this.dispose()
    } catch {
      // Even a compatibility-runtime cleanup failure must not suppress the
      // event that restores the independent static and accessible surface.
    }
    this.#canvas.dataset.live2dStatus = 'error'
    this.#canvas.dispatchEvent(new CustomEvent<Error>(RUNTIME_ERROR_EVENT, {
      detail: error,
    }))
  }
}

/**
 * Create a ready Live2D controller for the fixed local Elysia model.
 *
 * The promise resolves only after PurismCore, bounded model assets, texture
 * decoding, model consistency validation, and WebGL2 resources are ready. A
 * rejection is therefore safe for the React surface to replace with its
 * reviewed static-art fallback.
 */
export async function createLive2DController(
  canvas: HTMLCanvasElement,
  options: Live2DControllerOptions,
): Promise<Live2DController> {
  validateState(options.state)
  validateEmotion(options.emotion)
  validateFraming(options.framing)
  canvas.dataset.live2dStatus = 'loading'

  const gl = canvas.getContext('webgl2', {
    alpha: true,
    antialias: true,
    depth: false,
    failIfMajorPerformanceCaveat: true,
    powerPreference: 'high-performance',
    premultipliedAlpha: true,
    preserveDrawingBuffer: false,
    stencil: false,
  })
  if (gl === null) {
    canvas.dataset.live2dStatus = 'error'
    throw new Error('WebGL2 is unavailable for Live2D rendering.')
  }

  let model: PurismModel | null = null
  let moc: PurismMoc | null = null
  let controller: Live2DControllerImplementation | null = null
  try {
    const [core, assets] = await Promise.all([
      loadPurismCore(),
      loadFixedAssets(),
    ])
    const created = createCoreModel(core, assets.moc)
    model = created.model
    moc = created.moc
    const parameterIndices = validateCoreModel(model)
    controller = new Live2DControllerImplementation(
      canvas,
      gl,
      model,
      moc,
      parameterIndices,
      assets.texture,
      options,
    )
    await controller.initialize()
    return controller
  } catch (error) {
    if (controller !== null) {
      // Initialization can fail after allocating GPU resources or installing a
      // resize observer. Let the controller unwind every acquired capability.
      controller.dispose()
    } else {
      model?.release()
      if (moc !== null) {
        releaseMoc(moc)
      }
    }
    canvas.dataset.live2dStatus = 'error'
    throw asError(error)
  }
}
