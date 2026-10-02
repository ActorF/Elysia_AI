/**
 * Discover bounded external Live2D models without exposing native paths.
 *
 * The scanner reads only Cubism JSON manifests and filesystem metadata. It
 * never loads or executes programs shipped beside a model pack. Every asset
 * reference is resolved beneath both the selected library and its model
 * directory so a renderer-facing asset registry cannot become a file reader.
 */

import { createHash } from 'node:crypto'
import {
  lstat,
  open,
  opendir,
  realpath,
  type FileHandle,
} from 'node:fs/promises'
import path from 'node:path'

import {
  type DesktopPetModelSummary,
} from './desktop-pet-contracts.js'
import type {
  DesktopPetLibraryPresentation,
} from './desktop-pet-preferences.js'

/** Maximum directory levels traversed below one user-selected library root. */
export const DESKTOP_PET_LIBRARY_MAX_DEPTH = 16

/** Maximum entries examined before an unexpectedly broad folder is rejected. */
export const DESKTOP_PET_LIBRARY_MAX_ENTRIES = 20_000

/** Maximum candidate Cubism manifests admitted during one scan. */
export const DESKTOP_PET_LIBRARY_MAX_MANIFESTS = 256

/** Maximum UTF-8 bytes accepted for one Cubism model manifest. */
export const DESKTOP_PET_MANIFEST_MAXIMUM_BYTES = 256 * 1024

/** Maximum aggregate bytes referenced by one selectable external model. */
export const DESKTOP_PET_MODEL_MAXIMUM_BYTES = 96 * 1024 * 1024

const DESKTOP_PET_MOC_MAXIMUM_BYTES = 64 * 1024 * 1024
const DESKTOP_PET_TEXTURE_MAXIMUM_BYTES = 16 * 1024 * 1024
const DESKTOP_PET_JSON_RESOURCE_MAXIMUM_BYTES = 2 * 1024 * 1024
const DESKTOP_PET_MAX_TEXTURES = 4
const DESKTOP_PET_MAX_EXPRESSIONS = 64
const DESKTOP_PET_MAX_MOTION_GROUPS = 32
const DESKTOP_PET_MAX_MOTIONS = 256
const DESKTOP_PET_MAX_REFERENCE_CODE_UNITS = 1_024
const DESKTOP_PET_MAX_DISPLAY_NAME_CODE_POINTS = 200

/** Closed resource kinds later exposed through opaque custom-protocol tokens. */
export type DesktopPetModelResourceKind =
  | 'manifest'
  | 'moc'
  | 'texture'
  | 'physics'
  | 'pose'
  | 'display-info'
  | 'user-data'
  | 'expression'
  | 'motion'

/** Main-private verified file belonging to one discovered model. */
export interface DesktopPetModelResource {
  readonly token: string
  readonly kind: DesktopPetModelResourceKind
  readonly absolutePath: string
  readonly relativePath: string
  readonly sizeBytes: number
}

/** Main-private selectable model plus its renderer-safe summary fields. */
export interface DesktopPetModelDescriptor extends DesktopPetModelSummary {
  readonly manifestPath: string
  readonly manifestRelativePath: string
  readonly profile: string
  readonly resources: readonly DesktopPetModelResource[]
}

/** Complete result of scanning one canonical local model directory. */
export interface DesktopPetModelLibraryScan {
  readonly rootPath: string
  readonly folderName: string
  readonly models: readonly DesktopPetModelDescriptor[]
}

/** Report a missing or disconnected selected model directory. */
export class DesktopPetModelLibraryUnavailableError extends Error {}

/** Report an unsafe, damaged, or unreasonably broad model directory. */
export class DesktopPetModelLibraryValidationError extends Error {}

interface CandidateManifest {
  readonly absolutePath: string
  readonly displayName: string
  readonly groupKey: string
  readonly profile: string
  readonly profilePriority: number
  readonly relativePath: string
}

interface ManifestReferences {
  readonly Moc?: unknown
  readonly Textures?: unknown
  readonly Physics?: unknown
  readonly Pose?: unknown
  readonly DisplayInfo?: unknown
  readonly UserData?: unknown
  readonly Expressions?: unknown
  readonly Motions?: unknown
}

interface ModelManifest {
  readonly Version?: unknown
  readonly FileReferences?: ManifestReferences
}

interface ResourceReference {
  readonly kind: Exclude<DesktopPetModelResourceKind, 'manifest'>
  readonly value: string
  readonly suffix: string
  readonly maximumBytes: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isWithin(parent: string, candidate: string): boolean {
  const relative = path.relative(parent, candidate)
  return relative === '' || (
    relative !== '..'
    && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative)
  )
}

function normalizedRelativePath(rootPath: string, candidate: string): string {
  return path.relative(rootPath, candidate)
    .split(path.sep)
    .join('/')
    .normalize('NFC')
}

function opaqueId(prefix: 'model' | 'asset', identity: string): string {
  const digest = createHash('sha256').update(identity, 'utf8').digest('hex')
  return `${prefix}_${digest.slice(0, 32)}`
}

function compareCodePoints(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0
}

function codePointPrefix(value: string, maximum: number): string {
  return Array.from(value).slice(0, maximum).join('')
}

function safeDisplayName(value: string, fallback: string): string {
  const cleaned = value.replace(/\p{Cc}/gu, '').trim()
  if (cleaned.length === 0) {
    return fallback
  }
  return codePointPrefix(cleaned, DESKTOP_PET_MAX_DISPLAY_NAME_CODE_POINTS)
}

function candidateFromPath(
  absolutePath: string,
  rootPath: string,
): CandidateManifest {
  const relativePath = normalizedRelativePath(rootPath, absolutePath)
  const segments = relativePath.split('/')
  let imageIndex = -1
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    if (segments[index]?.toLocaleLowerCase('en-US') === 'img') {
      imageIndex = index
      break
    }
  }
  const hasPackProfile = imageIndex > 0
    && imageIndex + 3 < segments.length
    && segments[imageIndex + 2]?.toLocaleLowerCase('en-US') === 'cat_model'
  if (!hasPackProfile) {
    const fallbackName = path.basename(
      absolutePath,
      path.extname(absolutePath),
    )
    return Object.freeze({
      absolutePath,
      displayName: safeDisplayName(fallbackName, 'Live2D model'),
      groupKey: relativePath,
      profile: 'default',
      profilePriority: 3,
      relativePath,
    })
  }

  const profile = segments[imageIndex + 1]!.toLocaleLowerCase('en-US')
  const priority = profile === 'standard'
    ? 0
    : profile === 'keyboard'
      ? 1
      : profile === 'gamepad'
        ? 2
        : 3
  const groupKey = segments.slice(0, imageIndex).join('/')
  return Object.freeze({
    absolutePath,
    displayName: safeDisplayName(
      segments[imageIndex - 1]!,
      'Live2D model',
    ),
    groupKey,
    profile,
    profilePriority: priority,
    relativePath,
  })
}

async function readBoundedManifest(filePath: string): Promise<unknown> {
  let handle: FileHandle
  try {
    handle = await open(filePath, 'r')
  } catch {
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D manifest could not be opened.',
    )
  }
  try {
    let metadata
    try {
      metadata = await handle.stat()
    } catch {
      throw new DesktopPetModelLibraryValidationError(
        'A Live2D manifest changed while it was scanned.',
      )
    }
    if (
      !metadata.isFile()
      || metadata.size <= 0
      || metadata.size > DESKTOP_PET_MANIFEST_MAXIMUM_BYTES
    ) {
      throw new DesktopPetModelLibraryValidationError(
        'A Live2D manifest exceeds its safety limit.',
      )
    }
    const bytes = Buffer.alloc(metadata.size)
    let offset = 0
    while (offset < bytes.byteLength) {
      const result = await handle.read(
        bytes,
        offset,
        bytes.byteLength - offset,
        offset,
      )
      if (result.bytesRead === 0) {
        break
      }
      offset += result.bytesRead
    }
    if (offset !== bytes.byteLength) {
      throw new DesktopPetModelLibraryValidationError(
        'A Live2D manifest changed while it was scanned.',
      )
    }
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
      return JSON.parse(text) as unknown
    } catch {
      throw new DesktopPetModelLibraryValidationError(
        'A Live2D manifest is not valid UTF-8 JSON.',
      )
    }
  } finally {
    await handle.close().catch(() => {})
  }
}

function requireReference(
  value: unknown,
  fieldName: string,
): string {
  if (
    typeof value !== 'string'
    || value.length === 0
    || value.length > DESKTOP_PET_MAX_REFERENCE_CODE_UNITS
    || /[\p{Cc}\\%]/u.test(value)
  ) {
    throw new DesktopPetModelLibraryValidationError(
      `Live2D ${fieldName} reference is invalid.`,
    )
  }
  const segments = value.split('/')
  if (
    path.posix.isAbsolute(value)
    || path.win32.isAbsolute(value)
    || value.includes(':')
    || segments.some((segment) => (
      segment.length === 0 || segment === '.' || segment === '..'
    ))
  ) {
    throw new DesktopPetModelLibraryValidationError(
      `Live2D ${fieldName} reference leaves its model folder.`,
    )
  }
  return value
}

function collectResourceReferences(
  manifestValue: unknown,
): ResourceReference[] {
  if (!isRecord(manifestValue)) {
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D manifest must be a JSON object.',
    )
  }
  const manifest = manifestValue as ModelManifest
  const references = manifest.FileReferences
  if (
    manifest.Version !== 3
    || !isRecord(references)
  ) {
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D manifest does not use the supported Cubism 3 format.',
    )
  }

  const resources: ResourceReference[] = [{
    kind: 'moc',
    value: requireReference(references.Moc, 'Moc'),
    suffix: '.moc3',
    maximumBytes: DESKTOP_PET_MOC_MAXIMUM_BYTES,
  }]
  if (
    !Array.isArray(references.Textures)
    || references.Textures.length === 0
    || references.Textures.length > DESKTOP_PET_MAX_TEXTURES
  ) {
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D manifest has an unsupported texture list.',
    )
  }
  references.Textures.forEach((value, index) => {
    resources.push({
      kind: 'texture',
      value: requireReference(value, `texture ${index}`),
      suffix: '.png',
      maximumBytes: DESKTOP_PET_TEXTURE_MAXIMUM_BYTES,
    })
  })

  const optionalStrings: readonly [
    keyof ManifestReferences,
    DesktopPetModelResourceKind,
    string,
  ][] = [
    ['Physics', 'physics', '.physics3.json'],
    ['Pose', 'pose', '.pose3.json'],
    ['DisplayInfo', 'display-info', '.cdi3.json'],
    ['UserData', 'user-data', '.userdata3.json'],
  ]
  for (const [field, kind, suffix] of optionalStrings) {
    const value = references[field]
    if (value !== undefined) {
      resources.push({
        kind: kind as Exclude<DesktopPetModelResourceKind, 'manifest'>,
        value: requireReference(value, field),
        suffix,
        maximumBytes: DESKTOP_PET_JSON_RESOURCE_MAXIMUM_BYTES,
      })
    }
  }

  if (references.Expressions !== undefined) {
    if (
      !Array.isArray(references.Expressions)
      || references.Expressions.length > DESKTOP_PET_MAX_EXPRESSIONS
    ) {
      throw new DesktopPetModelLibraryValidationError(
        'A Live2D manifest has an unsupported expression list.',
      )
    }
    references.Expressions.forEach((expression, index) => {
      if (!isRecord(expression)) {
        throw new DesktopPetModelLibraryValidationError(
          'A Live2D expression entry is invalid.',
        )
      }
      resources.push({
        kind: 'expression',
        value: requireReference(expression.File, `expression ${index}`),
        suffix: '.exp3.json',
        maximumBytes: DESKTOP_PET_JSON_RESOURCE_MAXIMUM_BYTES,
      })
    })
  }

  if (references.Motions !== undefined) {
    if (
      !isRecord(references.Motions)
      || Reflect.ownKeys(references.Motions).length
        > DESKTOP_PET_MAX_MOTION_GROUPS
    ) {
      throw new DesktopPetModelLibraryValidationError(
        'A Live2D manifest has an unsupported motion table.',
      )
    }
    let motionCount = 0
    for (const group of Object.values(references.Motions)) {
      if (!Array.isArray(group)) {
        throw new DesktopPetModelLibraryValidationError(
          'A Live2D motion group is invalid.',
        )
      }
      for (const motion of group) {
        motionCount += 1
        if (
          motionCount > DESKTOP_PET_MAX_MOTIONS
          || !isRecord(motion)
        ) {
          throw new DesktopPetModelLibraryValidationError(
            'A Live2D manifest has too many motions.',
          )
        }
        resources.push({
          kind: 'motion',
          value: requireReference(motion.File, `motion ${motionCount}`),
          suffix: '.motion3.json',
          maximumBytes: DESKTOP_PET_JSON_RESOURCE_MAXIMUM_BYTES,
        })
      }
    }
  }
  return resources
}

async function resolveResource(
  reference: ResourceReference,
  modelDirectory: string,
  rootPath: string,
): Promise<DesktopPetModelResource> {
  if (!reference.value.toLocaleLowerCase('en-US').endsWith(reference.suffix)) {
    throw new DesktopPetModelLibraryValidationError(
      `A Live2D ${reference.kind} resource has an unsupported file type.`,
    )
  }
  const absolutePath = path.resolve(
    modelDirectory,
    ...reference.value.split('/'),
  )
  if (
    !isWithin(modelDirectory, absolutePath)
    || !isWithin(rootPath, absolutePath)
  ) {
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D resource leaves its selected model folder.',
    )
  }
  let metadata
  try {
    metadata = await lstat(absolutePath)
  } catch {
    throw new DesktopPetModelLibraryValidationError(
      `A required Live2D ${reference.kind} resource is missing.`,
    )
  }
  if (
    metadata.isSymbolicLink()
    || !metadata.isFile()
    || metadata.size <= 0
    || metadata.size > reference.maximumBytes
  ) {
    throw new DesktopPetModelLibraryValidationError(
      `A Live2D ${reference.kind} resource is unsafe or oversized.`,
    )
  }
  let canonicalPath: string
  try {
    canonicalPath = await realpath(absolutePath)
  } catch {
    throw new DesktopPetModelLibraryValidationError(
      `A Live2D ${reference.kind} resource cannot be resolved.`,
    )
  }
  if (
    !isWithin(modelDirectory, canonicalPath)
    || !isWithin(rootPath, canonicalPath)
  ) {
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D resource resolves outside its selected model folder.',
    )
  }
  const relativePath = normalizedRelativePath(rootPath, canonicalPath)
  return Object.freeze({
    token: opaqueId('asset', relativePath),
    kind: reference.kind,
    absolutePath: canonicalPath,
    relativePath,
    sizeBytes: metadata.size,
  })
}

async function validateCandidate(
  candidate: CandidateManifest,
  rootPath: string,
): Promise<DesktopPetModelDescriptor> {
  if (/[\p{Cc}\\/%]/u.test(path.basename(candidate.absolutePath))) {
    // The custom URL registry intentionally has one canonical spelling for
    // every route. Reject manifest names that would require a second percent
    // decode or introduce an invisible control character before persistence.
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D manifest name cannot be represented safely.',
    )
  }
  let manifestMetadata
  try {
    manifestMetadata = await lstat(candidate.absolutePath)
  } catch {
    // A removable or network-backed pack can change between directory
    // enumeration and validation. Treat that race as an invalid candidate so
    // it cannot escape as a native error containing the user's private path.
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D manifest changed while it was scanned.',
    )
  }
  if (
    manifestMetadata.isSymbolicLink()
    || !manifestMetadata.isFile()
    || manifestMetadata.size <= 0
    || manifestMetadata.size > DESKTOP_PET_MANIFEST_MAXIMUM_BYTES
  ) {
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D manifest is unsafe or oversized.',
    )
  }
  let canonicalManifest: string
  try {
    canonicalManifest = await realpath(candidate.absolutePath)
  } catch {
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D manifest could not be resolved safely.',
    )
  }
  if (!isWithin(rootPath, canonicalManifest)) {
    throw new DesktopPetModelLibraryValidationError(
      'A Live2D manifest resolves outside the selected folder.',
    )
  }
  const modelDirectory = path.dirname(canonicalManifest)
  const manifestValue = await readBoundedManifest(canonicalManifest)
  const references = collectResourceReferences(manifestValue)
  const resources: DesktopPetModelResource[] = []
  const seenPaths = new Set<string>()
  let totalBytes = manifestMetadata.size
  for (const reference of references) {
    const resource = await resolveResource(
      reference,
      modelDirectory,
      rootPath,
    )
    if (seenPaths.has(resource.absolutePath)) {
      continue
    }
    seenPaths.add(resource.absolutePath)
    totalBytes += resource.sizeBytes
    if (totalBytes > DESKTOP_PET_MODEL_MAXIMUM_BYTES) {
      throw new DesktopPetModelLibraryValidationError(
        'A Live2D model exceeds its aggregate safety limit.',
      )
    }
    resources.push(resource)
  }
  const manifestRelativePath = normalizedRelativePath(
    rootPath,
    canonicalManifest,
  )
  resources.unshift(Object.freeze({
    token: opaqueId('asset', manifestRelativePath),
    kind: 'manifest',
    absolutePath: canonicalManifest,
    relativePath: manifestRelativePath,
    sizeBytes: manifestMetadata.size,
  }))
  return Object.freeze({
    id: opaqueId('model', manifestRelativePath),
    displayName: candidate.displayName,
    manifestPath: canonicalManifest,
    manifestRelativePath,
    profile: candidate.profile,
    resources: Object.freeze(resources),
  })
}

async function collectCandidateManifests(
  rootPath: string,
): Promise<CandidateManifest[]> {
  const candidates: CandidateManifest[] = []
  const pending: { directory: string; depth: number }[] = [{
    directory: rootPath,
    depth: 0,
  }]
  let entriesSeen = 0
  while (pending.length > 0) {
    const current = pending.pop()!
    let directory
    try {
      directory = await opendir(current.directory)
    } catch {
      throw new DesktopPetModelLibraryValidationError(
        'A directory inside the Live2D folder could not be read.',
      )
    }
    try {
      for await (const entry of directory) {
        entriesSeen += 1
        if (entriesSeen > DESKTOP_PET_LIBRARY_MAX_ENTRIES) {
          throw new DesktopPetModelLibraryValidationError(
            'The selected Live2D folder contains too many entries.',
          )
        }
        if (entry.isSymbolicLink()) {
          // Links are ignored rather than followed, so a pack may contain an
          // unrelated shortcut without granting it access outside the picker.
          continue
        }
        const absolutePath = path.join(current.directory, entry.name)
        if (entry.isDirectory()) {
          if (current.depth >= DESKTOP_PET_LIBRARY_MAX_DEPTH) {
            throw new DesktopPetModelLibraryValidationError(
              'The selected Live2D folder is nested too deeply.',
            )
          }
          pending.push({
            directory: absolutePath,
            depth: current.depth + 1,
          })
          continue
        }
        if (
          !entry.isFile()
          || !entry.name.toLocaleLowerCase('en-US').endsWith('.model3.json')
        ) {
          continue
        }
        candidates.push(candidateFromPath(absolutePath, rootPath))
        if (candidates.length > DESKTOP_PET_LIBRARY_MAX_MANIFESTS) {
          throw new DesktopPetModelLibraryValidationError(
            'The selected folder contains too many Live2D manifests.',
          )
        }
      }
    } catch (error) {
      if (error instanceof DesktopPetModelLibraryValidationError) {
        throw error
      }
      throw new DesktopPetModelLibraryValidationError(
        'A directory inside the Live2D folder changed while it was scanned.',
        { cause: error },
      )
    }
  }
  return candidates
}

/**
 * Scan one native-picker directory and return only validated model entries.
 *
 * Bongo-style bundles commonly repeat each look for standard, keyboard, and
 * gamepad input. Candidates are grouped by the directory above `img`, with the
 * neutral standard profile preferred so Settings presents looks rather than
 * three input-device implementations of every look.
 */
export async function scanDesktopPetModelLibrary(
  libraryPath: string,
): Promise<DesktopPetModelLibraryScan> {
  if (
    typeof libraryPath !== 'string'
    || libraryPath.length === 0
    || !path.isAbsolute(libraryPath)
  ) {
    throw new DesktopPetModelLibraryValidationError(
      'Desktop Pet model folder must be an absolute path.',
    )
  }
  const requestedRoot = path.resolve(libraryPath)
  let rootMetadata
  try {
    rootMetadata = await lstat(requestedRoot)
  } catch {
    throw new DesktopPetModelLibraryUnavailableError(
      'The selected Desktop Pet model folder is unavailable.',
    )
  }
  if (rootMetadata.isSymbolicLink() || !rootMetadata.isDirectory()) {
    throw new DesktopPetModelLibraryValidationError(
      'Desktop Pet model folder must be a real directory.',
    )
  }
  let rootPath: string
  try {
    rootPath = await realpath(requestedRoot)
  } catch {
    throw new DesktopPetModelLibraryUnavailableError(
      'The selected Desktop Pet model folder is unavailable.',
    )
  }
  const candidates = await collectCandidateManifests(rootPath)
  const groups = new Map<string, CandidateManifest[]>()
  for (const candidate of candidates) {
    const group = groups.get(candidate.groupKey) ?? []
    group.push(candidate)
    groups.set(candidate.groupKey, group)
  }

  const models: DesktopPetModelDescriptor[] = []
  for (const groupKey of [...groups.keys()].sort(compareCodePoints)) {
    const group = groups.get(groupKey)!
    group.sort((left, right) => (
      left.profilePriority - right.profilePriority
      || compareCodePoints(left.relativePath, right.relativePath)
    ))
    let accepted: DesktopPetModelDescriptor | null = null
    for (const candidate of group) {
      try {
        accepted = await validateCandidate(candidate, rootPath)
        break
      } catch (error) {
        if (!(error instanceof DesktopPetModelLibraryValidationError)) {
          throw error
        }
      }
    }
    if (accepted !== null) {
      models.push(accepted)
    }
  }
  if (candidates.length > 0 && models.length === 0) {
    throw new DesktopPetModelLibraryValidationError(
      'No safe, complete Live2D model was found in the selected folder.',
    )
  }
  const ids = new Set(models.map((model) => model.id))
  if (ids.size !== models.length) {
    throw new DesktopPetModelLibraryValidationError(
      'The selected folder contains ambiguous Live2D model identities.',
    )
  }
  return Object.freeze({
    rootPath,
    folderName: safeDisplayName(path.basename(rootPath), 'Selected folder'),
    models: Object.freeze(models),
  })
}

/** Convert a private scan to the exact bounded state safe for Settings. */
export function presentDesktopPetModelLibrary(
  scan: DesktopPetModelLibraryScan,
): DesktopPetLibraryPresentation {
  return Object.freeze({
    status: scan.models.length === 0 ? 'empty' : 'ready',
    folderName: scan.folderName,
    models: Object.freeze(scan.models.map((model) => Object.freeze({
      id: model.id,
      displayName: model.displayName,
    }))),
  })
}

/** Find one selected private descriptor without accepting a native path. */
export function findDesktopPetModel(
  scan: DesktopPetModelLibraryScan,
  modelId: string,
): DesktopPetModelDescriptor | null {
  return scan.models.find((model) => model.id === modelId) ?? null
}
