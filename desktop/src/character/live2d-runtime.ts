/**
 * Load a validated local Cubism model through PurismCore and render it with a
 * bounded WebGL2 pipeline that has no third-party renderer dependency.
 */

import type { CharacterEmotion } from './character-emotion.ts'
import type { CharacterState } from './character-state.ts'

/** Closed camera profiles supported by local Cubism models. */
export type Live2DFraming = 'full-body' | 'half-body' | 'call-half-body'

/** Initial semantic values required to start the Live2D renderer. */
export interface Live2DControllerOptions {
  readonly emotion: CharacterEmotion
  readonly framing: Live2DFraming
  /**
   * Absolute or page-relative URL of the selected model3 manifest.
   *
   * Electron supplies an opaque allowlisted protocol URL for external models;
   * development callers may provide only a same-origin HTTP(S) URL.
   */
  readonly modelManifestUrl: string
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
  readonly Physics?: unknown
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
  readonly masks?: readonly Int32Array[]
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

interface ValidatedModelManifest extends ModelManifest {
  readonly FileReferences: ModelFileReferences & {
    readonly Moc: string
    readonly Physics?: string
    readonly Textures: readonly string[]
  }
}

interface LoadedAssets {
  readonly manifest: ValidatedModelManifest
  readonly moc: Uint8Array
  readonly physics: PhysicsRigDefinition | null
  readonly textures: readonly Uint8Array[]
}

interface ResolvedModelAssets {
  readonly manifest: ValidatedModelManifest
  readonly mocUrl: URL
  readonly physicsUrl: URL | null
  readonly textureUrls: readonly URL[]
}

type PhysicsValueType = 'X' | 'Y' | 'Angle'

interface PhysicsVector {
  readonly x: number
  readonly y: number
}

interface PhysicsNormalization {
  readonly minimum: number
  readonly defaultValue: number
  readonly maximum: number
}

interface PhysicsInputDefinition {
  readonly parameterId: string
  readonly reflect: boolean
  readonly type: PhysicsValueType
  readonly weight: number
}

interface PhysicsOutputDefinition {
  readonly parameterId: string
  readonly reflect: boolean
  readonly scale: number
  readonly type: PhysicsValueType
  readonly vertexIndex: number
  readonly weight: number
}

interface PhysicsParticleDefinition {
  readonly acceleration: number
  readonly delay: number
  readonly mobility: number
  readonly radius: number
}

interface PhysicsSettingDefinition {
  readonly inputs: readonly PhysicsInputDefinition[]
  readonly normalizationAngle: PhysicsNormalization
  readonly normalizationPosition: PhysicsNormalization
  readonly outputs: readonly PhysicsOutputDefinition[]
  readonly particles: readonly PhysicsParticleDefinition[]
}

interface PhysicsRigDefinition {
  readonly fps: number
  readonly gravity: PhysicsVector
  readonly settings: readonly PhysicsSettingDefinition[]
  readonly wind: PhysicsVector
}

interface PhysicsParticleState {
  readonly acceleration: number
  readonly delay: number
  readonly mobility: number
  readonly radius: number
  lastGravity: PhysicsVector
  lastPosition: PhysicsVector
  position: PhysicsVector
  velocity: PhysicsVector
}

interface PngHeader {
  readonly height: number
  readonly width: number
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
  readonly maskEnabled: WebGLUniformLocation
  readonly maskInverted: WebGLUniformLocation
  readonly maskTexture: WebGLUniformLocation
  readonly viewport: WebGLUniformLocation
}

interface MaskShaderLocations {
  readonly opacity: WebGLUniformLocation
  readonly texture: WebGLUniformLocation
  readonly transform: WebGLUniformLocation
}

interface MaskGpuResources {
  readonly framebuffer: WebGLFramebuffer
  readonly locations: MaskShaderLocations
  readonly program: WebGLProgram
  readonly texture: WebGLTexture
  height: number
  width: number
}

interface GpuResources {
  readonly program: WebGLProgram
  readonly vertexArray: WebGLVertexArrayObject
  readonly positionBuffer: WebGLBuffer
  readonly uvBuffer: WebGLBuffer
  readonly indexBuffer: WebGLBuffer
  readonly textures: readonly WebGLTexture[]
  readonly locations: ShaderLocations
  readonly mask: MaskGpuResources | null
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
const MODEL_MANIFEST_LIMIT = 256 * 1024
const MODEL_MOC_LIMIT = 64 * 1024 * 1024
const MODEL_PHYSICS_LIMIT = 2 * 1024 * 1024
const MODEL_TEXTURE_LIMIT = 16 * 1024 * 1024
const MODEL_TEXTURE_TOTAL_LIMIT = 64 * 1024 * 1024
const MODEL_TEXTURE_COUNT_LIMIT = 8
const MODEL_TEXTURE_DIMENSION_LIMIT = 4096
const MODEL_TEXTURE_PIXEL_LIMIT = 16 * 1024 * 1024
const MODEL_TEXTURE_TOTAL_PIXEL_LIMIT = 16 * 1024 * 1024
const PHYSICS_SETTING_LIMIT = 64
const PHYSICS_INPUT_LIMIT = 256
const PHYSICS_OUTPUT_LIMIT = 512
const PHYSICS_PARTICLE_LIMIT = 1024
const PHYSICS_PARTICLES_PER_SETTING_LIMIT = 64
const PHYSICS_IDENTIFIER_LIMIT = 256
const PHYSICS_ABSOLUTE_VALUE_LIMIT = 1_000_000
const PHYSICS_AIR_RESISTANCE = 5
const PHYSICS_MOVEMENT_THRESHOLD = 0.001
const PHYSICS_MAX_FIXED_STEPS = 8
const LOAD_TIMEOUT_MS = 10_000
const CORE_MEMORY_RESERVATION = 128 * 1024 * 1024
const MAX_DEVICE_PIXEL_RATIO = 2
const MAX_CANVAS_DIMENSION = 4096
const MAX_DRAWABLES = 512
const MAX_TOTAL_VERTICES = 1_000_000
const MAX_TOTAL_INDICES = 3_000_000
const MAX_MASKS_PER_DRAWABLE = 64
const MAX_TOTAL_MASK_REFERENCES = 4096
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

// Half-body surfaces retain the reviewed legacy crop. Full-body rendering is
// derived from immutable Core canvas metadata so differently authored desktop
// pet models fit without animation-driven camera jitter.
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
layout(location = 0) in vec2 a_position;
layout(location = 1) in vec2 a_uv;
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
uniform sampler2D u_mask_texture;
uniform int u_mask_enabled;
uniform int u_mask_inverted;
uniform vec2 u_viewport;
out vec4 output_color;

void main() {
  vec4 texel = texture(u_texture, v_uv);
  texel.rgb *= u_multiply_color.rgb;
  texel.rgb = (texel.rgb + u_screen_color.rgb * texel.a)
    - (texel.rgb * u_screen_color.rgb);
  float mask_alpha = 1.0;
  if (u_mask_enabled != 0) {
    mask_alpha = texture(u_mask_texture, gl_FragCoord.xy / u_viewport).a;
    if (u_mask_inverted != 0) {
      mask_alpha = 1.0 - mask_alpha;
    }
  }
  output_color = texel * u_base_color * mask_alpha;
}
`

const MASK_FRAGMENT_SHADER_SOURCE = `#version 300 es
precision mediump float;
in vec2 v_uv;
uniform sampler2D u_texture;
uniform float u_opacity;
out vec4 output_color;

void main() {
  float alpha = texture(u_texture, v_uv).a * u_opacity;
  if (alpha <= 0.001) {
    discard;
  }
  output_color = vec4(alpha);
}
`

let purismCorePromise: Promise<PurismCoreNamespace> | undefined
let coreMemoryReserved = false

function asError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value))
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
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

function validateAssetEndpoint(url: URL): void {
  if (
    url.username !== ''
    || url.password !== ''
    || url.search !== ''
    || url.hash !== ''
  ) {
    throw new Error('Live2D asset URLs cannot contain credentials or URL modifiers.')
  }
  if (url.protocol === 'http:' || url.protocol === 'https:') {
    if (url.origin !== window.location.origin) {
      throw new Error('Live2D development assets must remain same-origin.')
    }
    return
  }
  if (url.protocol === 'elysia-pet-asset:' && url.host === 'model') {
    return
  }
  if (url.protocol !== 'elysia-pet-asset:') {
    throw new Error(`Unsupported Live2D asset protocol: ${url.protocol}`)
  }
  throw new Error(`Unsupported Live2D asset authority: ${url.host}`)
}

function resolveManifestUrl(candidate: string): URL {
  if (typeof candidate !== 'string' || candidate.length === 0) {
    throw new TypeError('A Live2D model manifest URL is required.')
  }
  const manifest = new URL(candidate, document.baseURI)
  validateAssetEndpoint(manifest)
  return manifest
}

function resolveManifestReference(reference: string, manifestUrl: URL): URL {
  if (reference.length === 0 || reference.length > 1024) {
    throw new Error('Live2D manifest contains an invalid empty or oversized file reference.')
  }
  let decodedReference: string
  try {
    decodedReference = decodeURIComponent(reference)
  } catch (error) {
    throw new Error('Live2D manifest contains an invalid percent-encoded file reference.', {
      cause: error,
    })
  }
  const segments = decodedReference.split('/')
  if (
    decodedReference.startsWith('/')
    || decodedReference.includes('\\')
    || decodedReference.includes('\0')
    || decodedReference.includes('?')
    || decodedReference.includes('#')
    || segments.some((segment) => (
      segment === ''
      || segment === '.'
      || segment === '..'
      || segment.includes(':')
    ))
  ) {
    // A model3 reference is a path below its manifest. Keeping this invariant
    // prevents renderer-controlled JSON from widening Main's protocol grant.
    throw new Error(`Unsafe Live2D manifest file reference: ${reference}`)
  }

  const directory = new URL('./', manifestUrl)
  const resolved = new URL(reference, directory)
  validateAssetEndpoint(resolved)
  if (
    resolved.protocol !== directory.protocol
    || resolved.host !== directory.host
    || !resolved.pathname.startsWith(directory.pathname)
  ) {
    throw new Error(`Live2D manifest file reference escaped its model directory: ${reference}`)
  }
  return resolved
}

function validateManifest(
  value: unknown,
  manifestUrl: URL,
): ResolvedModelAssets {
  if (typeof value !== 'object' || value === null) {
    throw new Error('The Live2D model manifest must be a JSON object.')
  }
  const manifest = value as ModelManifest
  const references = manifest.FileReferences
  if (
    manifest.Version !== 3
    || typeof references !== 'object'
    || references === null
    || typeof references.Moc !== 'string'
    || (references.Physics !== undefined && typeof references.Physics !== 'string')
    || !Array.isArray(references.Textures)
    || references.Textures.length === 0
    || references.Textures.length > MODEL_TEXTURE_COUNT_LIMIT
    || references.Textures.some((texture) => typeof texture !== 'string')
  ) {
    throw new Error('The Live2D model manifest does not match the bounded asset contract.')
  }
  const validated = manifest as ValidatedModelManifest
  const mocUrl = resolveManifestReference(validated.FileReferences.Moc, manifestUrl)
  const physicsUrl = validated.FileReferences.Physics === undefined
    ? null
    : resolveManifestReference(validated.FileReferences.Physics, manifestUrl)
  const textureUrls = validated.FileReferences.Textures.map(
    (reference) => resolveManifestReference(reference, manifestUrl),
  )
  if (new Set(textureUrls.map((url) => url.href)).size !== textureUrls.length) {
    throw new Error('The Live2D model manifest contains duplicate textures.')
  }
  return { manifest: validated, mocUrl, physicsUrl, textureUrls }
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

async function fetchModelAsset(
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

function hasMoc3Header(bytes: Uint8Array): boolean {
  return bytes.byteLength >= 4
    && bytes[0] === 0x4d
    && bytes[1] === 0x4f
    && bytes[2] === 0x43
    && bytes[3] === 0x33
}

function readUint32BigEndian(bytes: Uint8Array, offset: number): number {
  return (bytes[offset]! * 0x1000000)
    + (bytes[offset + 1]! * 0x10000)
    + (bytes[offset + 2]! * 0x100)
    + bytes[offset + 3]!
}

/**
 * Read the fixed PNG signature and IHDR before invoking a browser decoder.
 *
 * Compressed byte limits do not bound a PNG's decoded allocation. Inspecting
 * its mandatory first chunk here prevents a tiny decompression-bomb header
 * from reaching `createImageBitmap` and allocating an attacker-chosen surface.
 */
function parsePngHeader(bytes: Uint8Array): PngHeader {
  if (
    bytes.byteLength < 33
    || bytes[0] !== 0x89
    || bytes[1] !== 0x50
    || bytes[2] !== 0x4e
    || bytes[3] !== 0x47
    || bytes[4] !== 0x0d
    || bytes[5] !== 0x0a
    || bytes[6] !== 0x1a
    || bytes[7] !== 0x0a
    || readUint32BigEndian(bytes, 8) !== 13
    || bytes[12] !== 0x49
    || bytes[13] !== 0x48
    || bytes[14] !== 0x44
    || bytes[15] !== 0x52
  ) {
    throw new Error('A selected Live2D texture has an invalid PNG IHDR.')
  }

  const width = readUint32BigEndian(bytes, 16)
  const height = readUint32BigEndian(bytes, 20)
  if (
    width <= 0
    || height <= 0
    || width > MODEL_TEXTURE_DIMENSION_LIMIT
    || height > MODEL_TEXTURE_DIMENSION_LIMIT
    || width * height > MODEL_TEXTURE_PIXEL_LIMIT
  ) {
    throw new Error('Live2D atlas dimensions exceed their fixed limit.')
  }

  const bitDepth = bytes[24]!
  const colorType = bytes[25]!
  const validBitDepth = (
    (colorType === 0 && [1, 2, 4, 8, 16].includes(bitDepth))
    || (colorType === 2 && (bitDepth === 8 || bitDepth === 16))
    || (colorType === 3 && [1, 2, 4, 8].includes(bitDepth))
    || (colorType === 4 && (bitDepth === 8 || bitDepth === 16))
    || (colorType === 6 && (bitDepth === 8 || bitDepth === 16))
  )
  if (
    !validBitDepth
    || bytes[26] !== 0
    || bytes[27] !== 0
    || (bytes[28] !== 0 && bytes[28] !== 1)
  ) {
    throw new Error('A selected Live2D texture uses an unsupported PNG format.')
  }
  return { height, width }
}

function validateTextureHeaders(textures: readonly Uint8Array[]): void {
  let totalPixels = 0
  for (const texture of textures) {
    const header = parsePngHeader(texture)
    totalPixels += header.width * header.height
    if (totalPixels > MODEL_TEXTURE_TOTAL_PIXEL_LIMIT) {
      throw new Error('The selected Live2D textures exceed their decoded pixel budget.')
    }
  }
}

function requirePhysicsNumber(
  value: unknown,
  field: string,
  minimum = -PHYSICS_ABSOLUTE_VALUE_LIMIT,
  maximum = PHYSICS_ABSOLUTE_VALUE_LIMIT,
): number {
  if (
    typeof value !== 'number'
    || !Number.isFinite(value)
    || value < minimum
    || value > maximum
  ) {
    throw new Error(`Live2D physics ${field} is invalid.`)
  }
  return value
}

function requirePhysicsCount(value: unknown, field: string, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0 || (value as number) > maximum) {
    throw new Error(`Live2D physics ${field} is invalid.`)
  }
  return value as number
}

function requirePhysicsIdentifier(value: unknown, field: string): string {
  if (
    typeof value !== 'string'
    || value.length === 0
    || value.length > PHYSICS_IDENTIFIER_LIMIT
    || /\p{Cc}/u.test(value)
  ) {
    throw new Error(`Live2D physics ${field} is invalid.`)
  }
  return value
}

function requirePhysicsVector(value: unknown, field: string): PhysicsVector {
  if (!isRecord(value)) {
    throw new Error(`Live2D physics ${field} is invalid.`)
  }
  return Object.freeze({
    x: requirePhysicsNumber(value.X, `${field}.X`),
    y: requirePhysicsNumber(value.Y, `${field}.Y`),
  })
}

function requirePhysicsNormalization(
  value: unknown,
  field: string,
): PhysicsNormalization {
  if (!isRecord(value)) {
    throw new Error(`Live2D physics ${field} is invalid.`)
  }
  const minimum = requirePhysicsNumber(value.Minimum, `${field}.Minimum`)
  const defaultValue = requirePhysicsNumber(value.Default, `${field}.Default`)
  const maximum = requirePhysicsNumber(value.Maximum, `${field}.Maximum`)
  if (minimum > defaultValue || defaultValue > maximum || minimum === maximum) {
    throw new Error(`Live2D physics ${field} range is invalid.`)
  }
  return Object.freeze({ defaultValue, maximum, minimum })
}

function requirePhysicsType(value: unknown, field: string): PhysicsValueType {
  if (value !== 'X' && value !== 'Y' && value !== 'Angle') {
    throw new Error(`Live2D physics ${field} is unsupported.`)
  }
  return value
}

/** Parse an untrusted physics3 document into a closed, allocation-bounded rig. */
function parsePhysicsRig(bytes: Uint8Array): PhysicsRigDefinition {
  let parsed: unknown
  try {
    parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  } catch (error) {
    throw new Error('The Live2D physics file is not valid UTF-8 JSON.', {
      cause: error,
    })
  }
  if (!isRecord(parsed) || parsed.Version !== 3 || !isRecord(parsed.Meta)) {
    throw new Error('The Live2D physics file does not use the supported format.')
  }
  const meta = parsed.Meta
  if (!isRecord(meta.EffectiveForces) || !Array.isArray(parsed.PhysicsSettings)) {
    throw new Error('The Live2D physics metadata is invalid.')
  }
  const settingCount = requirePhysicsCount(
    meta.PhysicsSettingCount,
    'setting count',
    PHYSICS_SETTING_LIMIT,
  )
  const expectedInputCount = requirePhysicsCount(
    meta.TotalInputCount,
    'input count',
    PHYSICS_INPUT_LIMIT,
  )
  const expectedOutputCount = requirePhysicsCount(
    meta.TotalOutputCount,
    'output count',
    PHYSICS_OUTPUT_LIMIT,
  )
  const expectedParticleCount = requirePhysicsCount(
    meta.VertexCount,
    'particle count',
    PHYSICS_PARTICLE_LIMIT,
  )
  if (settingCount !== parsed.PhysicsSettings.length) {
    throw new Error('The Live2D physics setting count does not match its metadata.')
  }

  let totalInputs = 0
  let totalOutputs = 0
  let totalParticles = 0
  const settings = parsed.PhysicsSettings.map((settingValue, settingIndex) => {
    if (
      !isRecord(settingValue)
      || !Array.isArray(settingValue.Input)
      || !Array.isArray(settingValue.Output)
      || !Array.isArray(settingValue.Vertices)
      || !isRecord(settingValue.Normalization)
      || settingValue.Vertices.length < 2
      || settingValue.Vertices.length > PHYSICS_PARTICLES_PER_SETTING_LIMIT
    ) {
      throw new Error(`Live2D physics setting ${settingIndex} is invalid.`)
    }
    totalInputs += settingValue.Input.length
    totalOutputs += settingValue.Output.length
    totalParticles += settingValue.Vertices.length
    if (
      totalInputs > PHYSICS_INPUT_LIMIT
      || totalOutputs > PHYSICS_OUTPUT_LIMIT
      || totalParticles > PHYSICS_PARTICLE_LIMIT
    ) {
      throw new Error('The Live2D physics rig exceeds its allocation limits.')
    }

    const inputs = settingValue.Input.map((inputValue, inputIndex) => {
      if (
        !isRecord(inputValue)
        || !isRecord(inputValue.Source)
        || inputValue.Source.Target !== 'Parameter'
        || typeof inputValue.Reflect !== 'boolean'
      ) {
        throw new Error(`Live2D physics input ${settingIndex}:${inputIndex} is invalid.`)
      }
      return Object.freeze({
        parameterId: requirePhysicsIdentifier(
          inputValue.Source.Id,
          `input ${settingIndex}:${inputIndex} id`,
        ),
        reflect: inputValue.Reflect,
        type: requirePhysicsType(inputValue.Type, `input ${settingIndex}:${inputIndex} type`),
        weight: requirePhysicsNumber(
          inputValue.Weight,
          `input ${settingIndex}:${inputIndex} weight`,
          0,
          100,
        ),
      })
    })
    const particles = settingValue.Vertices.map((particleValue, particleIndex) => {
      if (!isRecord(particleValue)) {
        throw new Error(`Live2D physics particle ${settingIndex}:${particleIndex} is invalid.`)
      }
      requirePhysicsVector(
        particleValue.Position,
        `particle ${settingIndex}:${particleIndex} position`,
      )
      return Object.freeze({
        acceleration: requirePhysicsNumber(
          particleValue.Acceleration,
          `particle ${settingIndex}:${particleIndex} acceleration`,
          0,
          100,
        ),
        delay: requirePhysicsNumber(
          particleValue.Delay,
          `particle ${settingIndex}:${particleIndex} delay`,
          0,
          10,
        ),
        mobility: requirePhysicsNumber(
          particleValue.Mobility,
          `particle ${settingIndex}:${particleIndex} mobility`,
          0,
          10,
        ),
        radius: requirePhysicsNumber(
          particleValue.Radius,
          `particle ${settingIndex}:${particleIndex} radius`,
          0,
          10_000,
        ),
      })
    })
    const outputs = settingValue.Output.map((outputValue, outputIndex) => {
      if (
        !isRecord(outputValue)
        || !isRecord(outputValue.Destination)
        || outputValue.Destination.Target !== 'Parameter'
        || typeof outputValue.Reflect !== 'boolean'
      ) {
        throw new Error(`Live2D physics output ${settingIndex}:${outputIndex} is invalid.`)
      }
      const vertexIndex = requirePhysicsCount(
        outputValue.VertexIndex,
        `output ${settingIndex}:${outputIndex} vertex`,
        particles.length - 1,
      )
      if (vertexIndex < 1) {
        throw new Error(`Live2D physics output ${settingIndex}:${outputIndex} vertex is invalid.`)
      }
      return Object.freeze({
        parameterId: requirePhysicsIdentifier(
          outputValue.Destination.Id,
          `output ${settingIndex}:${outputIndex} id`,
        ),
        reflect: outputValue.Reflect,
        scale: requirePhysicsNumber(
          outputValue.Scale,
          `output ${settingIndex}:${outputIndex} scale`,
        ),
        type: requirePhysicsType(outputValue.Type, `output ${settingIndex}:${outputIndex} type`),
        vertexIndex,
        weight: requirePhysicsNumber(
          outputValue.Weight,
          `output ${settingIndex}:${outputIndex} weight`,
          0,
          100,
        ),
      })
    })
    return Object.freeze({
      inputs: Object.freeze(inputs),
      normalizationAngle: requirePhysicsNormalization(
        settingValue.Normalization.Angle,
        `setting ${settingIndex} angle normalization`,
      ),
      normalizationPosition: requirePhysicsNormalization(
        settingValue.Normalization.Position,
        `setting ${settingIndex} position normalization`,
      ),
      outputs: Object.freeze(outputs),
      particles: Object.freeze(particles),
    })
  })
  if (
    totalInputs !== expectedInputCount
    || totalOutputs !== expectedOutputCount
    || totalParticles !== expectedParticleCount
  ) {
    throw new Error('The Live2D physics totals do not match their metadata.')
  }
  const fps = meta.Fps === undefined
    ? 0
    : requirePhysicsNumber(meta.Fps, 'frame rate', 1, 240)
  return Object.freeze({
    fps,
    gravity: requirePhysicsVector(meta.EffectiveForces.Gravity, 'gravity'),
    settings: Object.freeze(settings),
    wind: requirePhysicsVector(meta.EffectiveForces.Wind, 'wind'),
  })
}

async function loadModelAssets(manifestUrl: URL): Promise<LoadedAssets> {
  const abortController = new AbortController()
  const timeout = globalThis.setTimeout(() => {
    abortController.abort(new Error('Live2D asset loading timed out.'))
  }, LOAD_TIMEOUT_MS)

  try {
    const manifestBytes = await fetchModelAsset(
      manifestUrl,
      MODEL_MANIFEST_LIMIT,
      abortController.signal,
    )
    let parsedManifest: unknown
    try {
      parsedManifest = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(manifestBytes))
    } catch (error) {
      throw new Error('The Live2D model manifest is not valid UTF-8 JSON.', {
        cause: error,
      })
    }
    const resolved = validateManifest(parsedManifest, manifestUrl)
    const [moc, textures, physicsBytes] = await Promise.all([
      fetchModelAsset(resolved.mocUrl, MODEL_MOC_LIMIT, abortController.signal),
      Promise.all(resolved.textureUrls.map((url) => (
        fetchModelAsset(url, MODEL_TEXTURE_LIMIT, abortController.signal)
      ))),
      resolved.physicsUrl === null
        ? Promise.resolve(null)
        : fetchModelAsset(
            resolved.physicsUrl,
            MODEL_PHYSICS_LIMIT,
            abortController.signal,
          ),
    ])
    if (!hasMoc3Header(moc)) {
      throw new Error('The selected Live2D moc does not have a MOC3 header.')
    }
    validateTextureHeaders(textures)
    const totalTextureBytes = textures.reduce(
      (total, texture) => total + texture.byteLength,
      0,
    )
    if (totalTextureBytes > MODEL_TEXTURE_TOTAL_LIMIT) {
      throw new Error('The selected Live2D textures exceed their aggregate size limit.')
    }

    return {
      manifest: resolved.manifest,
      moc,
      physics: physicsBytes === null ? null : parsePhysicsRig(physicsBytes),
      textures,
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
    throw new Error('PurismCore rejected the selected Live2D moc.')
  }
  try {
    const consistency = moc.hasMocConsistency(buffer)
    if (consistency !== 1 && consistency !== true) {
      throw new Error('The selected Live2D moc failed its consistency check.')
    }

    // Consistency checking allocates temporary WASM memory. Constructing the
    // model afterwards is essential because its public arrays must be created
    // only after that possible memory growth has finished.
    const model = core.Model.fromMoc(moc)
    if (model === null) {
      throw new Error('PurismCore could not instantiate the selected Live2D model.')
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

function validateCoreModel(
  model: PurismModel,
  textureCount: number,
): ReadonlyMap<string, number> {
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
  if (
    drawables.masks !== undefined
    && drawables.masks.length !== drawables.count
  ) {
    throw new Error('PurismCore returned mismatched Live2D mask arrays.')
  }
  if (Array.from(drawables.textureIndices).some((index) => (
    !Number.isInteger(index) || index < 0 || index >= textureCount
  ))) {
    throw new Error('A Live2D drawable selects an unavailable texture atlas.')
  }

  let totalVertices = 0
  let totalIndices = 0
  let totalMaskReferences = 0
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
    const maskCount = drawables.maskCounts[index] ?? -1
    const masks = drawables.masks?.[index]
    if (
      !Number.isInteger(maskCount)
      || maskCount < 0
      || maskCount > MAX_MASKS_PER_DRAWABLE
      || (maskCount > 0 && masks === undefined)
      || (masks !== undefined && masks.length !== maskCount)
      || masks?.some((maskIndex) => (
        !Number.isInteger(maskIndex)
        || maskIndex < 0
        || maskIndex >= drawables.count
      )) === true
    ) {
      throw new Error(`Live2D drawable ${index} has invalid clipping masks.`)
    }
    totalVertices += vertexCount
    totalIndices += indexCount
    totalMaskReferences += maskCount
  }
  if (totalVertices > MAX_TOTAL_VERTICES || totalIndices > MAX_TOTAL_INDICES) {
    throw new Error('The Live2D mesh exceeds its fixed geometry limits.')
  }
  if (totalMaskReferences > MAX_TOTAL_MASK_REFERENCES) {
    throw new Error('The Live2D model exceeds its clipping-mask limit.')
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
  const header = parsePngHeader(bytes)
  const blob = new Blob([exactArrayBuffer(bytes)], { type: 'image/png' })
  if (typeof globalThis.createImageBitmap === 'function') {
    const bitmap = await globalThis.createImageBitmap(blob, {
      colorSpaceConversion: 'default',
      imageOrientation: 'none',
      premultiplyAlpha: 'premultiply',
    })
    if (bitmap.width !== header.width || bitmap.height !== header.height) {
      bitmap.close()
      throw new Error('The decoded Live2D texture does not match its PNG IHDR.')
    }
    return bitmap
  }

  const objectUrl = URL.createObjectURL(blob)
  try {
    const image = new Image()
    image.decoding = 'async'
    image.src = objectUrl
    await image.decode()
    if (image.width !== header.width || image.height !== header.height) {
      throw new Error('The decoded Live2D texture does not match its PNG IHDR.')
    }
    return image
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

async function decodeTextures(
  textureBytes: readonly Uint8Array[],
): Promise<readonly DecodedTexture[]> {
  const decoded: DecodedTexture[] = []
  try {
    for (const bytes of textureBytes) {
      // Decode sequentially so one malformed atlas cannot leave several large
      // browser decoders and bitmaps alive after Promise.all rejects early.
      decoded.push(await decodeTexture(bytes))
    }
    return decoded
  } catch (error) {
    for (const texture of decoded) closeDecodedTexture(texture)
    throw error
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

function linkProgram(
  gl: WebGL2RenderingContext,
  fragmentSource: string,
): WebGLProgram {
  const vertexShader = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER_SOURCE)
  let fragmentShader: WebGLShader
  try {
    fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource)
  } catch (error) {
    gl.deleteShader(vertexShader)
    throw error
  }
  const program = gl.createProgram()
  if (program === null) {
    gl.deleteShader(vertexShader)
    gl.deleteShader(fragmentShader)
    throw new Error('WebGL2 could not allocate a Live2D shader program.')
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
  return program
}

function createGpuResources(
  gl: WebGL2RenderingContext,
  decodedTextures: readonly DecodedTexture[],
  needsClippingMasks: boolean,
): GpuResources {
  for (const decodedTexture of decodedTextures) {
    if (
      decodedTexture.width <= 0
      || decodedTexture.height <= 0
      || decodedTexture.width > MODEL_TEXTURE_DIMENSION_LIMIT
      || decodedTexture.height > MODEL_TEXTURE_DIMENSION_LIMIT
      || decodedTexture.width * decodedTexture.height > MODEL_TEXTURE_PIXEL_LIMIT
    ) {
      throw new Error('Live2D atlas dimensions exceed their fixed limit.')
    }
  }

  const program = linkProgram(gl, FRAGMENT_SHADER_SOURCE)

  const vertexArray = gl.createVertexArray()
  const positionBuffer = gl.createBuffer()
  const uvBuffer = gl.createBuffer()
  const indexBuffer = gl.createBuffer()
  const textures = decodedTextures.map(() => gl.createTexture())
  if (
    vertexArray === null
    || positionBuffer === null
    || uvBuffer === null
    || indexBuffer === null
    || textures.some((texture) => texture === null)
  ) {
    gl.deleteProgram(program)
    if (vertexArray !== null) gl.deleteVertexArray(vertexArray)
    if (positionBuffer !== null) gl.deleteBuffer(positionBuffer)
    if (uvBuffer !== null) gl.deleteBuffer(uvBuffer)
    if (indexBuffer !== null) gl.deleteBuffer(indexBuffer)
    for (const texture of textures) {
      if (texture !== null) gl.deleteTexture(texture)
    }
    throw new Error('WebGL2 could not allocate Live2D drawing resources.')
  }
  const allocatedTextures = textures as WebGLTexture[]
  let mask: MaskGpuResources | null = null

  const releaseAllocations = (): void => {
    if (mask !== null) {
      gl.deleteFramebuffer(mask.framebuffer)
      gl.deleteTexture(mask.texture)
      gl.deleteProgram(mask.program)
    }
    gl.deleteProgram(program)
    gl.deleteVertexArray(vertexArray)
    gl.deleteBuffer(positionBuffer)
    gl.deleteBuffer(uvBuffer)
    gl.deleteBuffer(indexBuffer)
    for (const texture of allocatedTextures) gl.deleteTexture(texture)
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
      maskEnabled: requireUniform(gl, program, 'u_mask_enabled'),
      maskInverted: requireUniform(gl, program, 'u_mask_inverted'),
      maskTexture: requireUniform(gl, program, 'u_mask_texture'),
      viewport: requireUniform(gl, program, 'u_viewport'),
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
    decodedTextures.forEach((decodedTexture, index) => {
      gl.bindTexture(gl.TEXTURE_2D, allocatedTextures[index] ?? null)
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
    })

    if (needsClippingMasks) {
      const maskProgram = linkProgram(gl, MASK_FRAGMENT_SHADER_SOURCE)
      const maskTexture = gl.createTexture()
      const maskFramebuffer = gl.createFramebuffer()
      if (maskTexture === null || maskFramebuffer === null) {
        if (maskTexture !== null) gl.deleteTexture(maskTexture)
        if (maskFramebuffer !== null) gl.deleteFramebuffer(maskFramebuffer)
        gl.deleteProgram(maskProgram)
        throw new Error('WebGL2 could not allocate Live2D clipping resources.')
      }
      let maskLocations: MaskShaderLocations
      try {
        maskLocations = {
          opacity: requireUniform(gl, maskProgram, 'u_opacity'),
          texture: requireUniform(gl, maskProgram, 'u_texture'),
          transform: requireUniform(gl, maskProgram, 'u_transform'),
        }
      } catch (error) {
        gl.deleteFramebuffer(maskFramebuffer)
        gl.deleteTexture(maskTexture)
        gl.deleteProgram(maskProgram)
        throw error
      }
      mask = {
        framebuffer: maskFramebuffer,
        height: 0,
        locations: maskLocations,
        program: maskProgram,
        texture: maskTexture,
        width: 0,
      }
    }
    gl.bindVertexArray(null)
    gl.bindTexture(gl.TEXTURE_2D, null)

    return {
      program,
      vertexArray,
      positionBuffer,
      uvBuffer,
      indexBuffer,
      textures: allocatedTextures,
      locations,
      mask,
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
  if (resources.mask !== null) {
    gl.deleteFramebuffer(resources.mask.framebuffer)
    gl.deleteTexture(resources.mask.texture)
    gl.deleteProgram(resources.mask.program)
  }
  for (const texture of resources.textures) gl.deleteTexture(texture)
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

function physicsVector(x: number, y: number): PhysicsVector {
  return { x, y }
}

function normalizePhysicsVector(value: PhysicsVector, fallback: PhysicsVector): PhysicsVector {
  const length = Math.hypot(value.x, value.y)
  if (!Number.isFinite(length) || length <= Number.EPSILON) {
    return physicsVector(fallback.x, fallback.y)
  }
  return physicsVector(value.x / length, value.y / length)
}

function physicsDirectionToRadians(from: PhysicsVector, to: PhysicsVector): number {
  let result = Math.atan2(to.y, to.x) - Math.atan2(from.y, from.x)
  while (result < -Math.PI) result += Math.PI * 2
  while (result > Math.PI) result -= Math.PI * 2
  return result
}

function normalizePhysicsParameter(
  value: number,
  parameterMinimum: number,
  parameterMaximum: number,
  normalization: PhysicsNormalization,
  reflect: boolean,
): number {
  const minimum = Math.min(parameterMinimum, parameterMaximum)
  const maximum = Math.max(parameterMinimum, parameterMaximum)
  // Cubism Physics normalizes around the midpoint of the Core range, not the
  // parameter's authored default. This distinction matters for asymmetric
  // defaults and keeps the pendulum input compatible with physics3 output.
  const defaultValue = minimum + (maximum - minimum) / 2
  const bounded = clamp(value, minimum, maximum)
  let normalized = normalization.defaultValue
  if (bounded > defaultValue && maximum > defaultValue) {
    normalized += (bounded - defaultValue)
      * (normalization.maximum - normalization.defaultValue)
      / (maximum - defaultValue)
  } else if (bounded < defaultValue && minimum < defaultValue) {
    normalized += (bounded - defaultValue)
      * (normalization.minimum - normalization.defaultValue)
      / (minimum - defaultValue)
  }
  // Cubism's Reflect flag chooses the authored sign; false intentionally
  // negates the normalized input rather than meaning "leave unchanged".
  return reflect ? normalized : -normalized
}

function createPhysicsParticleStates(
  particles: readonly PhysicsParticleDefinition[],
): PhysicsParticleState[] {
  const states: PhysicsParticleState[] = []
  let y = 0
  for (let index = 0; index < particles.length; index += 1) {
    const particle = particles[index]!
    if (index > 0) y += particle.radius
    const position = physicsVector(0, y)
    states.push({
      acceleration: particle.acceleration,
      delay: particle.delay,
      lastGravity: physicsVector(0, 1),
      lastPosition: physicsVector(position.x, position.y),
      mobility: particle.mobility,
      position,
      radius: particle.radius,
      velocity: physicsVector(0, 0),
    })
  }
  return states
}

/**
 * Evaluate the bounded Physics 3 pendulum graph against Core parameters.
 *
 * Every setting is processed in document order so an authored output can feed
 * a later setting during the same step. Particles retain velocity between
 * frames, while normalization and output blending use Core's declared bounds;
 * this preserves the behavior encoded by the model without evaluating scripts
 * or allocating from untrusted counts during animation.
 */
class PhysicsEvaluator {
  readonly #definition: PhysicsRigDefinition
  readonly #parameterIndices: ReadonlyMap<string, number>
  readonly #particleStates: readonly PhysicsParticleState[][]
  #remainingTime = 0

  constructor(
    definition: PhysicsRigDefinition,
    parameterIndices: ReadonlyMap<string, number>,
  ) {
    this.#definition = definition
    this.#parameterIndices = parameterIndices
    this.#particleStates = definition.settings.map((setting) => (
      createPhysicsParticleStates(setting.particles)
    ))
  }

  evaluate(parameters: PurismParameters, deltaSeconds: number): void {
    if (this.#definition.fps <= 0) {
      this.#step(parameters, deltaSeconds)
      return
    }
    const fixedStep = 1 / this.#definition.fps
    this.#remainingTime = Math.min(
      this.#remainingTime + deltaSeconds,
      fixedStep * PHYSICS_MAX_FIXED_STEPS,
    )
    let steps = 0
    while (
      this.#remainingTime >= fixedStep
      && steps < PHYSICS_MAX_FIXED_STEPS
    ) {
      this.#step(parameters, fixedStep)
      this.#remainingTime -= fixedStep
      steps += 1
    }
  }

  #step(parameters: PurismParameters, deltaSeconds: number): void {
    for (
      let settingIndex = 0;
      settingIndex < this.#definition.settings.length;
      settingIndex += 1
    ) {
      const setting = this.#definition.settings[settingIndex]!
      const particles = this.#particleStates[settingIndex]!
      let translationX = 0
      let translationY = 0
      let totalAngle = 0
      for (const input of setting.inputs) {
        const parameterIndex = this.#parameterIndices.get(input.parameterId)
        if (parameterIndex === undefined) continue
        const normalization = input.type === 'Angle'
          ? setting.normalizationAngle
          : setting.normalizationPosition
        const value = normalizePhysicsParameter(
          parameters.values[parameterIndex]!,
          parameters.minimumValues[parameterIndex]!,
          parameters.maximumValues[parameterIndex]!,
          normalization,
          input.reflect,
        ) * (input.weight / 100)
        if (input.type === 'X') translationX += value
        else if (input.type === 'Y') translationY += value
        else totalAngle += value
      }

      const inputRadians = -totalAngle * Math.PI / 180
      const rotatedX = translationX * Math.cos(inputRadians)
        - translationY * Math.sin(inputRadians)
      const rotatedY = translationX * Math.sin(inputRadians)
        + translationY * Math.cos(inputRadians)
      this.#updateParticles(
        particles,
        physicsVector(rotatedX, rotatedY),
        totalAngle,
        setting,
        deltaSeconds,
      )
      for (const output of setting.outputs) {
        this.#applyOutput(parameters, particles, output)
      }
    }
  }

  #updateParticles(
    particles: readonly PhysicsParticleState[],
    translation: PhysicsVector,
    totalAngle: number,
    setting: PhysicsSettingDefinition,
    deltaSeconds: number,
  ): void {
    particles[0]!.position = physicsVector(translation.x, translation.y)
    const angleRadians = totalAngle * Math.PI / 180
    const currentGravity = normalizePhysicsVector(
      physicsVector(Math.sin(angleRadians), Math.cos(angleRadians)),
      physicsVector(0, 1),
    )
    const threshold = PHYSICS_MOVEMENT_THRESHOLD * Math.max(
      Math.abs(setting.normalizationPosition.minimum),
      Math.abs(setting.normalizationPosition.maximum),
    )
    for (let index = 1; index < particles.length; index += 1) {
      const particle = particles[index]!
      const parent = particles[index - 1]!
      const previousPosition = particle.position
      particle.lastPosition = physicsVector(previousPosition.x, previousPosition.y)
      const delay = particle.delay * deltaSeconds * 30
      const direction = physicsVector(
        particle.position.x - parent.position.x,
        particle.position.y - parent.position.y,
      )
      const gravityRotation = physicsDirectionToRadians(
        particle.lastGravity,
        currentGravity,
      ) / PHYSICS_AIR_RESISTANCE
      const rotatedDirection = physicsVector(
        Math.cos(gravityRotation) * direction.x
          - Math.sin(gravityRotation) * direction.y,
        Math.sin(gravityRotation) * direction.x
          + Math.cos(gravityRotation) * direction.y,
      )
      let position = physicsVector(
        parent.position.x + rotatedDirection.x,
        parent.position.y + rotatedDirection.y,
      )
      const force = physicsVector(
        currentGravity.x * particle.acceleration + this.#definition.wind.x,
        currentGravity.y * particle.acceleration + this.#definition.wind.y,
      )
      position = physicsVector(
        position.x + particle.velocity.x * delay + force.x * delay * delay,
        position.y + particle.velocity.y * delay + force.y * delay * delay,
      )
      const constrainedDirection = normalizePhysicsVector(
        physicsVector(position.x - parent.position.x, position.y - parent.position.y),
        currentGravity,
      )
      position = physicsVector(
        parent.position.x + constrainedDirection.x * particle.radius,
        parent.position.y + constrainedDirection.y * particle.radius,
      )
      if (Math.abs(position.x) < threshold) {
        position = physicsVector(0, position.y)
      }
      if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) {
        throw new Error('Live2D physics produced a non-finite particle position.')
      }
      particle.position = position
      particle.velocity = delay <= Number.EPSILON
        ? physicsVector(0, 0)
        : physicsVector(
            (position.x - particle.lastPosition.x) / delay * particle.mobility,
            (position.y - particle.lastPosition.y) / delay * particle.mobility,
          )
      particle.lastGravity = currentGravity
    }
  }

  #applyOutput(
    parameters: PurismParameters,
    particles: readonly PhysicsParticleState[],
    output: PhysicsOutputDefinition,
  ): void {
    const parameterIndex = this.#parameterIndices.get(output.parameterId)
    if (parameterIndex === undefined) return
    const particle = particles[output.vertexIndex]!
    const parent = particles[output.vertexIndex - 1]!
    const translation = physicsVector(
      particle.position.x - parent.position.x,
      particle.position.y - parent.position.y,
    )
    let value: number
    if (output.type === 'X') {
      value = translation.x
    } else if (output.type === 'Y') {
      value = translation.y
    } else {
      const parentDirection = output.vertexIndex >= 2
        ? physicsVector(
            parent.position.x - particles[output.vertexIndex - 2]!.position.x,
            parent.position.y - particles[output.vertexIndex - 2]!.position.y,
          )
        : physicsVector(
            -this.#definition.gravity.x,
            -this.#definition.gravity.y,
          )
      value = physicsDirectionToRadians(parentDirection, translation)
    }
    if (output.reflect) value *= -1
    const target = clamp(
      value * output.scale,
      parameters.minimumValues[parameterIndex]!,
      parameters.maximumValues[parameterIndex]!,
    )
    const weight = output.weight / 100
    const current = parameters.values[parameterIndex]!
    const next = weight >= 1 ? target : current * (1 - weight) + target * weight
    if (!Number.isFinite(next)) {
      throw new Error('Live2D physics produced a non-finite parameter value.')
    }
    parameters.values[parameterIndex] = next
  }
}

class Live2DControllerImplementation implements Live2DController {
  readonly #canvas: HTMLCanvasElement
  readonly #gl: WebGL2RenderingContext
  readonly #model: PurismModel
  readonly #moc: PurismMoc
  readonly #parameterIndices: ReadonlyMap<string, number>
  readonly #physics: PhysicsEvaluator | null
  readonly #textureBytes: readonly Uint8Array[]
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
    physics: PhysicsRigDefinition | null,
    textureBytes: readonly Uint8Array[],
    options: Live2DControllerOptions,
  ) {
    this.#canvas = canvas
    this.#gl = gl
    this.#model = model
    this.#moc = moc
    this.#parameterIndices = parameterIndices
    this.#physics = physics === null
      ? null
      : new PhysicsEvaluator(physics, parameterIndices)
    this.#textureBytes = textureBytes
    this.#framing = options.framing
    this.#state = options.state
    this.#emotion = options.emotion
  }

  async initialize(): Promise<void> {
    const decodedTextures = await decodeTextures(this.#textureBytes)
    try {
      this.#gpu = createGpuResources(
        this.#gl,
        decodedTextures,
        Array.from(this.#model.drawables.maskCounts).some((count) => count > 0),
      )
    } finally {
      for (const texture of decodedTextures) closeDecodedTexture(texture)
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
      const decodedTextures = await decodeTextures(this.#textureBytes)
      let gpu: GpuResources
      try {
        gpu = createGpuResources(
          this.#gl,
          decodedTextures,
          Array.from(this.#model.drawables.maskCounts).some((count) => count > 0),
        )
      } finally {
        for (const texture of decodedTextures) closeDecodedTexture(texture)
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

    // The external pet has no access to trusted speech playback or the main
    // renderer DOM. Keeping mouth-open at its neutral value prevents a local
    // model from becoming an accidental cross-window audio side channel.
    this.#setParameter('ParamMouthOpenY', 0, 1)
    this.#physics?.evaluate(this.#model.parameters, deltaSeconds)
  }

  #resizeAndGetTransform(): readonly [number, number, number, number] {
    this.#resizeCanvas()
    const canvasInfo = this.#model.canvasinfo
    const camera = this.#framing === 'full-body'
      ? {
          centerX: (
            canvasInfo.CanvasWidth / 2 - canvasInfo.CanvasOriginX
          ) / canvasInfo.PixelsPerUnit,
          centerY: (
            canvasInfo.CanvasOriginY - canvasInfo.CanvasHeight / 2
          ) / canvasInfo.PixelsPerUnit,
          height: canvasInfo.CanvasHeight / canvasInfo.PixelsPerUnit * 1.04,
          width: canvasInfo.CanvasWidth / canvasInfo.PixelsPerUnit * 1.04,
        }
      : CAMERA_BY_FRAMING[this.#framing]
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

  #uploadDrawableMesh(resources: GpuResources, drawableIndex: number): void {
    const gl = this.#gl
    const drawables = this.#model.drawables
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
  }

  #bindDrawableTexture(resources: GpuResources, drawableIndex: number): void {
    const textureIndex = this.#model.drawables.textureIndices[drawableIndex] ?? -1
    const texture = resources.textures[textureIndex]
    if (texture === undefined) {
      throw new Error(`Live2D drawable ${drawableIndex} selected a missing texture.`)
    }
    this.#gl.activeTexture(this.#gl.TEXTURE0)
    this.#gl.bindTexture(this.#gl.TEXTURE_2D, texture)
  }

  #ensureClippingTarget(mask: MaskGpuResources): void {
    const gl = this.#gl
    gl.activeTexture(gl.TEXTURE0 + 1)
    gl.bindTexture(gl.TEXTURE_2D, mask.texture)
    if (mask.width !== this.#canvas.width || mask.height !== this.#canvas.height) {
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        this.#canvas.width,
        this.#canvas.height,
        0,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        null,
      )
      mask.width = this.#canvas.width
      mask.height = this.#canvas.height
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, mask.framebuffer)
    gl.framebufferTexture2D(
      gl.FRAMEBUFFER,
      gl.COLOR_ATTACHMENT0,
      gl.TEXTURE_2D,
      mask.texture,
      0,
    )
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null)
      throw new Error('WebGL2 rejected the Live2D clipping framebuffer.')
    }
  }

  #renderClippingMask(
    resources: GpuResources,
    drawableIndex: number,
    transform: readonly [number, number, number, number],
  ): void {
    const mask = resources.mask
    const maskDrawables = this.#model.drawables.masks?.[drawableIndex]
    if (mask === null || maskDrawables === undefined) {
      throw new Error(`Live2D drawable ${drawableIndex} has no clipping resources.`)
    }
    const gl = this.#gl
    const drawables = this.#model.drawables
    this.#ensureClippingTarget(mask)
    gl.viewport(0, 0, this.#canvas.width, this.#canvas.height)
    gl.disable(gl.DEPTH_TEST)
    gl.disable(gl.SCISSOR_TEST)
    gl.disable(gl.CULL_FACE)
    gl.enable(gl.BLEND)
    gl.blendFuncSeparate(
      gl.ONE,
      gl.ONE_MINUS_SRC_ALPHA,
      gl.ONE,
      gl.ONE_MINUS_SRC_ALPHA,
    )
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
    gl.useProgram(mask.program)
    gl.bindVertexArray(resources.vertexArray)
    gl.uniform1i(mask.locations.texture, 0)
    gl.uniform4f(mask.locations.transform, ...transform)

    for (const maskDrawableIndex of maskDrawables) {
      const opacity = drawables.opacities[maskDrawableIndex] ?? 0
      const indexCount = drawables.indexCounts[maskDrawableIndex] ?? 0
      if (opacity <= 0 || indexCount <= 0) {
        continue
      }
      this.#bindDrawableTexture(resources, maskDrawableIndex)
      this.#uploadDrawableMesh(resources, maskDrawableIndex)
      gl.uniform1f(mask.locations.opacity, opacity)
      gl.drawElements(gl.TRIANGLES, indexCount, gl.UNSIGNED_SHORT, 0)
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null)
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

    gl.bindFramebuffer(gl.FRAMEBUFFER, null)
    gl.viewport(0, 0, this.#canvas.width, this.#canvas.height)
    gl.disable(gl.DEPTH_TEST)
    gl.disable(gl.STENCIL_TEST)
    gl.disable(gl.SCISSOR_TEST)
    gl.enable(gl.BLEND)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
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
      const maskCount = drawables.maskCounts[drawableIndex] ?? 0
      if (maskCount > 0) {
        this.#renderClippingMask(resources, drawableIndex, transform)
      }

      gl.bindFramebuffer(gl.FRAMEBUFFER, null)
      gl.viewport(0, 0, this.#canvas.width, this.#canvas.height)
      gl.useProgram(resources.program)
      gl.bindVertexArray(resources.vertexArray)
      gl.uniform1i(resources.locations.texture, 0)
      gl.uniform1i(resources.locations.maskTexture, 1)
      gl.uniform1i(resources.locations.maskEnabled, maskCount > 0 ? 1 : 0)
      gl.uniform1i(
        resources.locations.maskInverted,
        maskCount > 0 && (constantFlags & 8) !== 0 ? 1 : 0,
      )
      gl.uniform2f(
        resources.locations.viewport,
        this.#canvas.width,
        this.#canvas.height,
      )
      gl.uniform4f(resources.locations.transform, ...transform)
      if (maskCount > 0 && resources.mask !== null) {
        gl.activeTexture(gl.TEXTURE0 + 1)
        gl.bindTexture(gl.TEXTURE_2D, resources.mask.texture)
      }
      this.#bindDrawableTexture(resources, drawableIndex)

      if ((constantFlags & 4) !== 0) {
        gl.disable(gl.CULL_FACE)
      } else {
        gl.enable(gl.CULL_FACE)
        gl.frontFace(gl.CCW)
        gl.cullFace(gl.BACK)
      }
      this.#configureBlendMode(constantFlags)
      this.#uploadDrawableMesh(resources, drawableIndex)

      const colorOffset = drawableIndex * 4
      // The texture upload and blend function both use premultiplied alpha.
      // Premultiplying this per-drawable tint as well prevents a fading
      // drawable (notably the blink crossfade) from retaining bright RGB at
      // near-zero alpha and producing a pale fringe over the replacement art.
      gl.uniform4f(
        resources.locations.baseColor,
        opacity,
        opacity,
        opacity,
        opacity,
      )
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
    gl.activeTexture(gl.TEXTURE0 + 1)
    gl.bindTexture(gl.TEXTURE_2D, null)
    gl.activeTexture(gl.TEXTURE0)
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
 * Create a ready Live2D controller for one bounded local Cubism model.
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
  const manifestUrl = resolveManifestUrl(options.modelManifestUrl)
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
      loadModelAssets(manifestUrl),
    ])
    const created = createCoreModel(core, assets.moc)
    model = created.model
    moc = created.moc
    const parameterIndices = validateCoreModel(model, assets.textures.length)
    controller = new Live2DControllerImplementation(
      canvas,
      gl,
      model,
      moc,
      parameterIndices,
      assets.physics,
      assets.textures,
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
