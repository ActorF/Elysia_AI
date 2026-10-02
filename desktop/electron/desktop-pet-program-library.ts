/**
 * Discover trusted local Bongo Cat Mver desktop-pet programs.
 *
 * The user chooses a folder, but that choice is not permission to execute an
 * arbitrary executable found below it. This scanner admits only the reviewed
 * Bongo Cat program build: its launcher, UI executable, and every loadable
 * top-level DLL must match pinned SHA-256 identities. Model data may differ,
 * provided the three input profiles remain complete and cannot escape the
 * selected program directory through links or relative references.
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

import type { DesktopPetModelSummary } from './desktop-pet-contracts.js'
import type {
  DesktopPetLibraryPresentation,
} from './desktop-pet-preferences.js'

/** Maximum directory levels traversed below one selected program root. */
export const DESKTOP_PET_PROGRAM_LIBRARY_MAX_DEPTH = 16

/** Maximum directory entries examined during one program scan. */
export const DESKTOP_PET_PROGRAM_LIBRARY_MAX_ENTRIES = 20_000

/** Maximum launcher-shaped candidate directories admitted during one scan. */
export const DESKTOP_PET_PROGRAM_LIBRARY_MAX_CANDIDATES = 64

/** Maximum UTF-8 bytes accepted for the program's mutable configuration. */
export const DESKTOP_PET_PROGRAM_CONFIG_MAXIMUM_BYTES = 256 * 1024

/** Maximum UTF-8 bytes accepted for one required Cubism manifest. */
export const DESKTOP_PET_PROGRAM_MANIFEST_MAXIMUM_BYTES = 256 * 1024

const DESKTOP_PET_PROGRAM_MAX_TOP_LEVEL_ENTRIES = 128
const DESKTOP_PET_PROGRAM_MAX_MODEL_REFERENCES = 512
const DESKTOP_PET_PROGRAM_MAX_REFERENCE_CODE_UNITS = 1_024
const DESKTOP_PET_PROGRAM_MAX_REFERENCED_FILE_BYTES = 64 * 1024 * 1024
const DESKTOP_PET_PROGRAM_MAX_DISPLAY_NAME_CODE_POINTS = 200
const HASH_BUFFER_BYTES = 64 * 1024

const TRUSTED_LAUNCHER = Object.freeze({
  sizeBytes: 1_523_200,
  sha256: '4D416349B00E1C1948082E3C0923FB654A4CF24F648EA3B56AE40FA39360EF8A',
})

const TRUSTED_FIXED_CODE_FILES = Object.freeze({
  'BongoCatMverUI.dll': Object.freeze({
    sizeBytes: 4_368_896,
    sha256: '126C8CD67FA59C8B13261FF640638E4006AE202F23A50DCEEF605014FC1706E5',
  }),
  'BongoCatUI.exe': Object.freeze({
    sizeBytes: 274_944,
    sha256: 'FDEC5F6642CB5A3B63DE798CB358D7602D7051F76BE84915727B06D30820D7FA',
  }),
  'd3dx10_43.dll': Object.freeze({
    sizeBytes: 470_880,
    sha256: '56FCD13650FD1F075743154E8C48465DD68A236AB8960667D75373139D2631BF',
  }),
  'MaterialDesignColors.dll': Object.freeze({
    sizeBytes: 299_520,
    sha256: 'EA0BC53E2F812EB73A8E035FCF7059EE1561822BA5985AB71CF332EABD09421D',
  }),
  'MaterialDesignThemes.Wpf.dll': Object.freeze({
    sizeBytes: 6_969_344,
    sha256: '66DBBA478DE4B692AC3715775A3522D7F092BBACA16513DB128DE3A544DCCC83',
  }),
  'msvcp140.dll': Object.freeze({
    sizeBytes: 450_320,
    sha256: '02C7259456EAC8CBADFB460377BA68E98282400C7A4A9D0BF49B3313EF6D554D',
  }),
  'openal32.dll': Object.freeze({
    sizeBytes: 669_696,
    sha256: 'E0C2F182672B706659B32B91EFBDC5B57A75D24368F5D8D860C130DE218AEDE7',
  }),
  'sfml-audio-2.dll': Object.freeze({
    sizeBytes: 1_012_224,
    sha256: '767ABFC88553AA78F9F707E9E3DACE73A59A10B840E5F02721D784F9246FD2C3',
  }),
  'sfml-graphics-2.dll': Object.freeze({
    sizeBytes: 811_520,
    sha256: 'B1EC9CDC18F0553B7BF18AFBB6EC23B5608CBBB086D583135371AB3597871B43',
  }),
  'sfml-network-2.dll': Object.freeze({
    sizeBytes: 132_096,
    sha256: 'E5F41C24899DD300DBC00739AA541908E0F0885B7B95CC1C4DCEF09EB9AABFBA',
  }),
  'sfml-system-2.dll': Object.freeze({
    sizeBytes: 50_176,
    sha256: 'AFB94EF85C8B663D331B5BD14DD0307EA127DC2794E20E532C8AE19942D08AAD',
  }),
  'sfml-window-2.dll': Object.freeze({
    sizeBytes: 123_392,
    sha256: '97E07E9CE9CEDC36AC8B66A54BD378CFFB808987EFCB827230BD6F321E770FC4',
  }),
  'vcruntime140_1.dll': Object.freeze({
    sizeBytes: 44_312,
    sha256: '6CC4315DACEB0522816C60678344466CB452426267F70C7FAAE925361674E774',
  }),
  'vcruntime140.dll': Object.freeze({
    sizeBytes: 83_224,
    sha256: 'FF9C1123CFF493A8F5EACB91115611B6C1C808B30C82AF9B6F388C0EF1F6B46D',
  }),
} satisfies Readonly<Record<string, TrustedFileIdentity>>)

const TRUSTED_RESOURCE_FILES = Object.freeze({
  'Resources/cat.ttf': Object.freeze({
    sizeBytes: 2_484_256,
    sha256: '3FE0466B89136A432EF0ECF32098506E407C94F6DEBF1770B1E5DB971F5C74EC',
  }),
  'Resources/l2dlogo.png': Object.freeze({
    sizeBytes: 42_153,
    sha256: '613EE26751461F5AAF1AE0CD2F152422841A8B846048C044B38964D9F7FD428D',
  }),
} satisfies Readonly<Record<string, TrustedFileIdentity>>)

const REQUIRED_MODEL_PROFILES = Object.freeze([
  'standard',
  'keyboard',
  'gamepad',
])

interface TrustedFileIdentity {
  readonly sizeBytes: number
  readonly sha256: string
}

interface CandidateProgram {
  readonly programDirectory: string
  readonly launcherName: string
  readonly relativeDirectory: string
}

interface CubismManifestReferences {
  readonly Moc?: unknown
  readonly Textures?: unknown
  readonly Physics?: unknown
  readonly Pose?: unknown
  readonly DisplayInfo?: unknown
  readonly UserData?: unknown
  readonly Expressions?: unknown
  readonly Motions?: unknown
}

interface CubismManifest {
  readonly Version?: unknown
  readonly FileReferences?: CubismManifestReferences
}

/** Main-private trusted program plus its renderer-safe summary fields. */
export interface DesktopPetProgramDescriptor extends DesktopPetModelSummary {
  readonly programDirectory: string
  readonly executablePath: string
}

/** Complete result of scanning one canonical local program directory. */
export interface DesktopPetProgramLibraryScan {
  readonly rootPath: string
  readonly folderName: string
  readonly programs: readonly DesktopPetProgramDescriptor[]
}

/** Report a missing or disconnected selected program directory. */
export class DesktopPetProgramLibraryUnavailableError extends Error {}

/** Report an unsafe, modified, or unreasonably broad program directory. */
export class DesktopPetProgramLibraryValidationError extends Error {}

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

function isSameNativePath(left: string, right: string): boolean {
  return path.relative(left, right) === '' && path.relative(right, left) === ''
}

function normalizedRelativePath(rootPath: string, candidate: string): string {
  const relative = path.relative(rootPath, candidate)
  return (relative.length === 0 ? '.' : relative)
    .split(path.sep)
    .join('/')
    .normalize('NFC')
}

function compareCodePoints(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0
}

function safeDisplayName(value: string): string {
  // Format controls include bidi overrides that could make one detected local
  // directory impersonate another in Settings even though opaque IDs differ.
  const cleaned = value.replace(/[\p{Cc}\p{Cf}]/gu, '').trim()
  if (cleaned.length === 0) {
    return 'Desktop Pet program'
  }
  return Array.from(cleaned)
    .slice(0, DESKTOP_PET_PROGRAM_MAX_DISPLAY_NAME_CODE_POINTS)
    .join('')
}

function programId(relativeDirectory: string): string {
  // The mutable config is deliberately absent from identity. A user's input,
  // window, and expression settings therefore survive rescans and upgrades of
  // Elysia, while moving a whole selected pack keeps its selections stable.
  const identity = `${relativeDirectory}\0${TRUSTED_LAUNCHER.sha256}`
  const digest = createHash('sha256').update(identity, 'utf8').digest('hex')
  return `model_${digest.slice(0, 32)}`
}

async function canonicalDirectory(
  directoryPath: string,
  rootPath: string,
  label: string,
): Promise<string> {
  let metadata
  try {
    metadata = await lstat(directoryPath)
  } catch {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} directory is missing.`,
    )
  }
  if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} directory is unsafe.`,
    )
  }
  let canonicalPath: string
  try {
    canonicalPath = await realpath(directoryPath)
  } catch {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} directory cannot be resolved.`,
    )
  }
  if (!isSameNativePath(canonicalPath, path.resolve(directoryPath))) {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} directory is redirected.`,
    )
  }
  if (!isWithin(rootPath, canonicalPath)) {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} directory leaves the selected folder.`,
    )
  }
  return canonicalPath
}

async function canonicalFile(
  filePath: string,
  programDirectory: string,
  rootPath: string,
  maximumBytes: number,
  label: string,
): Promise<{ readonly path: string; readonly sizeBytes: number }> {
  let metadata
  try {
    metadata = await lstat(filePath)
  } catch {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} file is missing.`,
    )
  }
  if (
    metadata.isSymbolicLink()
    || !metadata.isFile()
    || metadata.size <= 0
    || metadata.size > maximumBytes
  ) {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} file is unsafe or oversized.`,
    )
  }
  let canonicalPath: string
  try {
    canonicalPath = await realpath(filePath)
  } catch {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} file cannot be resolved.`,
    )
  }
  if (!isSameNativePath(canonicalPath, path.resolve(filePath))) {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} file is redirected.`,
    )
  }
  if (
    !isWithin(programDirectory, canonicalPath)
    || !isWithin(rootPath, canonicalPath)
  ) {
    throw new DesktopPetProgramLibraryValidationError(
      `A required Desktop Pet ${label} file leaves its program folder.`,
    )
  }
  return Object.freeze({ path: canonicalPath, sizeBytes: metadata.size })
}

async function readHandleExactly(
  handle: FileHandle,
  sizeBytes: number,
): Promise<Buffer> {
  const bytes = Buffer.alloc(sizeBytes)
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
    throw new DesktopPetProgramLibraryValidationError(
      'A Desktop Pet file changed while it was scanned.',
    )
  }
  return bytes
}

async function readBoundedJson(
  filePath: string,
  maximumBytes: number,
  label: string,
): Promise<unknown> {
  let handle: FileHandle
  try {
    handle = await open(filePath, 'r')
  } catch {
    throw new DesktopPetProgramLibraryValidationError(
      `The Desktop Pet ${label} file could not be opened.`,
    )
  }
  try {
    const metadata = await handle.stat()
    if (
      !metadata.isFile()
      || metadata.size <= 0
      || metadata.size > maximumBytes
    ) {
      throw new DesktopPetProgramLibraryValidationError(
        `The Desktop Pet ${label} file exceeds its safety limit.`,
      )
    }
    const bytes = await readHandleExactly(handle, metadata.size)
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
      return JSON.parse(text) as unknown
    } catch {
      throw new DesktopPetProgramLibraryValidationError(
        `The Desktop Pet ${label} file is not valid UTF-8 JSON.`,
      )
    }
  } finally {
    await handle.close().catch(() => {})
  }
}

async function hashFile(filePath: string, expectedSize: number): Promise<string> {
  let handle: FileHandle
  try {
    handle = await open(filePath, 'r')
  } catch {
    throw new DesktopPetProgramLibraryValidationError(
      'A trusted Desktop Pet program file could not be opened.',
    )
  }
  try {
    const metadata = await handle.stat()
    if (!metadata.isFile() || metadata.size !== expectedSize) {
      throw new DesktopPetProgramLibraryValidationError(
        'A trusted Desktop Pet program file has an unexpected size.',
      )
    }
    const digest = createHash('sha256')
    const buffer = Buffer.allocUnsafe(HASH_BUFFER_BYTES)
    let offset = 0
    while (offset < expectedSize) {
      const result = await handle.read(
        buffer,
        0,
        Math.min(buffer.byteLength, expectedSize - offset),
        offset,
      )
      if (result.bytesRead === 0) {
        throw new DesktopPetProgramLibraryValidationError(
          'A trusted Desktop Pet program file changed while it was scanned.',
        )
      }
      digest.update(buffer.subarray(0, result.bytesRead))
      offset += result.bytesRead
    }
    const finalMetadata = await handle.stat()
    if (!finalMetadata.isFile() || finalMetadata.size !== expectedSize) {
      throw new DesktopPetProgramLibraryValidationError(
        'A trusted Desktop Pet program file changed while it was scanned.',
      )
    }
    return digest.digest('hex').toUpperCase()
  } finally {
    await handle.close().catch(() => {})
  }
}

async function verifyPinnedFile(
  filePath: string,
  identity: TrustedFileIdentity,
  programDirectory: string,
  rootPath: string,
  label: string,
): Promise<string> {
  const file = await canonicalFile(
    filePath,
    programDirectory,
    rootPath,
    identity.sizeBytes,
    label,
  )
  if (file.sizeBytes !== identity.sizeBytes) {
    throw new DesktopPetProgramLibraryValidationError(
      `The Desktop Pet ${label} file does not match the reviewed build.`,
    )
  }
  const digest = await hashFile(file.path, identity.sizeBytes)
  if (digest !== identity.sha256) {
    throw new DesktopPetProgramLibraryValidationError(
      `The Desktop Pet ${label} file does not match the reviewed build.`,
    )
  }
  return file.path
}

async function validateTopLevelCodeSurface(
  programDirectory: string,
  launcherName: string,
): Promise<void> {
  const trustedNames = new Set([
    launcherName,
    ...Object.keys(TRUSTED_FIXED_CODE_FILES),
  ])
  let directory
  try {
    directory = await opendir(programDirectory)
  } catch {
    throw new DesktopPetProgramLibraryValidationError(
      'A Desktop Pet program directory could not be read.',
    )
  }
  let entriesSeen = 0
  try {
    for await (const entry of directory) {
      entriesSeen += 1
      if (entriesSeen > DESKTOP_PET_PROGRAM_MAX_TOP_LEVEL_ENTRIES) {
        throw new DesktopPetProgramLibraryValidationError(
          'A Desktop Pet program contains too many top-level entries.',
        )
      }
      if (entry.isSymbolicLink()) {
        throw new DesktopPetProgramLibraryValidationError(
          'A Desktop Pet program contains a top-level link.',
        )
      }
      if (
        /\.(?:dll|exe)$/iu.test(entry.name)
        && !trustedNames.has(entry.name)
      ) {
        // Windows searches the program directory for dependent libraries. An
        // extra DLL is therefore executable input, not harmless model data.
        throw new DesktopPetProgramLibraryValidationError(
          'A Desktop Pet program contains unreviewed executable code.',
        )
      }
    }
  } finally {
    await directory.close().catch(() => {})
  }
}

async function validateConfig(
  programDirectory: string,
  rootPath: string,
): Promise<void> {
  const file = await canonicalFile(
    path.join(programDirectory, 'config.json'),
    programDirectory,
    rootPath,
    DESKTOP_PET_PROGRAM_CONFIG_MAXIMUM_BYTES,
    'configuration',
  )
  const value = await readBoundedJson(
    file.path,
    DESKTOP_PET_PROGRAM_CONFIG_MAXIMUM_BYTES,
    'configuration',
  )
  if (!isRecord(value)) {
    throw new DesktopPetProgramLibraryValidationError(
      'The Desktop Pet configuration must be a JSON object.',
    )
  }
  for (const field of [
    'decoration',
    'standard',
    'keyboard',
    'gamepad',
    'network',
    'workarea',
  ]) {
    if (!isRecord(value[field])) {
      throw new DesktopPetProgramLibraryValidationError(
        'The Desktop Pet configuration is missing a required section.',
      )
    }
  }
  if (!Number.isSafeInteger(value.mode) || (value.mode as number) < 0 || (value.mode as number) > 2) {
    throw new DesktopPetProgramLibraryValidationError(
      'The Desktop Pet configuration has an invalid input mode.',
    )
  }
}

function requireModelReference(value: unknown, label: string): string {
  if (
    typeof value !== 'string'
    || value.length === 0
    || value.length > DESKTOP_PET_PROGRAM_MAX_REFERENCE_CODE_UNITS
    || /[\p{Cc}\\]/u.test(value)
  ) {
    throw new DesktopPetProgramLibraryValidationError(
      `A Desktop Pet ${label} reference is invalid.`,
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
    throw new DesktopPetProgramLibraryValidationError(
      `A Desktop Pet ${label} reference leaves its model folder.`,
    )
  }
  return value
}

function collectModelReferences(value: unknown): string[] {
  if (!isRecord(value)) {
    throw new DesktopPetProgramLibraryValidationError(
      'A Desktop Pet model manifest must be a JSON object.',
    )
  }
  const manifest = value as CubismManifest
  if (manifest.Version !== 3 || !isRecord(manifest.FileReferences)) {
    throw new DesktopPetProgramLibraryValidationError(
      'A Desktop Pet model does not use the supported Cubism 3 format.',
    )
  }
  const references = manifest.FileReferences
  const paths: string[] = [requireModelReference(references.Moc, 'Moc')]
  if (
    !Array.isArray(references.Textures)
    || references.Textures.length === 0
    || references.Textures.length > 16
  ) {
    throw new DesktopPetProgramLibraryValidationError(
      'A Desktop Pet model has an unsupported texture list.',
    )
  }
  references.Textures.forEach((texture, index) => {
    paths.push(requireModelReference(texture, `texture ${index}`))
  })
  for (const field of [
    'Physics',
    'Pose',
    'DisplayInfo',
    'UserData',
  ] as const) {
    const reference = references[field]
    if (reference !== undefined) {
      paths.push(requireModelReference(reference, field))
    }
  }
  if (references.Expressions !== undefined) {
    if (!Array.isArray(references.Expressions) || references.Expressions.length > 128) {
      throw new DesktopPetProgramLibraryValidationError(
        'A Desktop Pet model has an unsupported expression list.',
      )
    }
    references.Expressions.forEach((expression, index) => {
      if (!isRecord(expression)) {
        throw new DesktopPetProgramLibraryValidationError(
          'A Desktop Pet expression entry is invalid.',
        )
      }
      paths.push(requireModelReference(expression.File, `expression ${index}`))
    })
  }
  if (references.Motions !== undefined) {
    if (!isRecord(references.Motions) || Reflect.ownKeys(references.Motions).length > 64) {
      throw new DesktopPetProgramLibraryValidationError(
        'A Desktop Pet model has an unsupported motion table.',
      )
    }
    for (const motionGroup of Object.values(references.Motions)) {
      if (!Array.isArray(motionGroup)) {
        throw new DesktopPetProgramLibraryValidationError(
          'A Desktop Pet motion group is invalid.',
        )
      }
      for (const motion of motionGroup) {
        if (!isRecord(motion)) {
          throw new DesktopPetProgramLibraryValidationError(
            'A Desktop Pet motion entry is invalid.',
          )
        }
        paths.push(requireModelReference(motion.File, 'motion'))
      }
    }
  }
  if (paths.length > DESKTOP_PET_PROGRAM_MAX_MODEL_REFERENCES) {
    throw new DesktopPetProgramLibraryValidationError(
      'A Desktop Pet model references too many files.',
    )
  }
  return paths
}

async function validateModelProfile(
  programDirectory: string,
  rootPath: string,
  profile: string,
): Promise<void> {
  const imageDirectory = await canonicalDirectory(
    path.join(programDirectory, 'img'),
    rootPath,
    'image',
  )
  if (!isWithin(programDirectory, imageDirectory)) {
    throw new DesktopPetProgramLibraryValidationError(
      'The Desktop Pet image directory leaves its program folder.',
    )
  }
  const profileDirectory = await canonicalDirectory(
    path.join(imageDirectory, profile),
    rootPath,
    `${profile} profile`,
  )
  const modelDirectory = await canonicalDirectory(
    path.join(profileDirectory, 'cat_model'),
    rootPath,
    `${profile} model`,
  )
  if (!isWithin(programDirectory, modelDirectory)) {
    throw new DesktopPetProgramLibraryValidationError(
      'A Desktop Pet model directory leaves its program folder.',
    )
  }
  const manifestFile = await canonicalFile(
    path.join(modelDirectory, 'cat.model3.json'),
    programDirectory,
    rootPath,
    DESKTOP_PET_PROGRAM_MANIFEST_MAXIMUM_BYTES,
    `${profile} model manifest`,
  )
  const manifest = await readBoundedJson(
    manifestFile.path,
    DESKTOP_PET_PROGRAM_MANIFEST_MAXIMUM_BYTES,
    `${profile} model manifest`,
  )
  for (const reference of collectModelReferences(manifest)) {
    const referencedPath = path.resolve(
      modelDirectory,
      ...reference.split('/'),
    )
    if (!isWithin(modelDirectory, referencedPath)) {
      throw new DesktopPetProgramLibraryValidationError(
        'A Desktop Pet model reference leaves its model folder.',
      )
    }
    await canonicalFile(
      referencedPath,
      programDirectory,
      rootPath,
      DESKTOP_PET_PROGRAM_MAX_REFERENCED_FILE_BYTES,
      `${profile} model resource`,
    )
  }
}

async function validateCandidate(
  candidate: CandidateProgram,
  rootPath: string,
): Promise<DesktopPetProgramDescriptor> {
  const directoryName = path.basename(candidate.programDirectory)
  if (
    directoryName.length === 0
    || directoryName.length > 240
    || /\p{Cc}/u.test(directoryName)
  ) {
    throw new DesktopPetProgramLibraryValidationError(
      'A Desktop Pet program directory has an invalid name.',
    )
  }
  const programDirectory = await canonicalDirectory(
    candidate.programDirectory,
    rootPath,
    'program',
  )
  await validateTopLevelCodeSurface(programDirectory, candidate.launcherName)
  await validateConfig(programDirectory, rootPath)
  for (const profile of REQUIRED_MODEL_PROFILES) {
    await validateModelProfile(programDirectory, rootPath, profile)
  }
  await canonicalDirectory(
    path.join(programDirectory, 'Resources'),
    rootPath,
    'resources',
  )
  const executablePath = await verifyPinnedFile(
    path.join(programDirectory, candidate.launcherName),
    TRUSTED_LAUNCHER,
    programDirectory,
    rootPath,
    'launcher',
  )
  for (const [fileName, identity] of Object.entries(TRUSTED_FIXED_CODE_FILES)) {
    await verifyPinnedFile(
      path.join(programDirectory, fileName),
      identity,
      programDirectory,
      rootPath,
      fileName,
    )
  }
  for (const [relativePath, identity] of Object.entries(TRUSTED_RESOURCE_FILES)) {
    await verifyPinnedFile(
      path.join(programDirectory, ...relativePath.split('/')),
      identity,
      programDirectory,
      rootPath,
      relativePath,
    )
  }
  return Object.freeze({
    id: programId(candidate.relativeDirectory),
    displayName: safeDisplayName(directoryName),
    programDirectory,
    executablePath,
  })
}

async function collectCandidatePrograms(
  rootPath: string,
): Promise<CandidateProgram[]> {
  const candidates: CandidateProgram[] = []
  const pending: { readonly directory: string; readonly depth: number }[] = [{
    directory: rootPath,
    depth: 0,
  }]
  let entriesSeen = 0
  while (pending.length > 0) {
    const current = pending.pop()!
    const currentDirectory = await canonicalDirectory(
      current.directory,
      rootPath,
      'library',
    )
    let directory
    try {
      directory = await opendir(currentDirectory)
    } catch {
      throw new DesktopPetProgramLibraryValidationError(
        'A directory inside the Desktop Pet folder could not be read.',
      )
    }
    const childDirectories: string[] = []
    const launcherName = `A${path.basename(currentDirectory)}.exe`
    let hasLauncher = false
    try {
      for await (const entry of directory) {
        entriesSeen += 1
        if (entriesSeen > DESKTOP_PET_PROGRAM_LIBRARY_MAX_ENTRIES) {
          throw new DesktopPetProgramLibraryValidationError(
            'The selected Desktop Pet folder contains too many entries.',
          )
        }
        if (entry.isSymbolicLink()) {
          // Links are never traversed. The original program resolves assets
          // relative to its working directory, so accepting a linked package
          // would make the picker an unintended filesystem capability.
          continue
        }
        if (entry.isFile() && entry.name === launcherName) {
          hasLauncher = true
          continue
        }
        if (entry.isDirectory()) {
          childDirectories.push(path.join(currentDirectory, entry.name))
        }
      }
    } finally {
      await directory.close().catch(() => {})
    }
    if (hasLauncher) {
      candidates.push(Object.freeze({
        programDirectory: currentDirectory,
        launcherName,
        relativeDirectory: normalizedRelativePath(rootPath, currentDirectory),
      }))
      if (candidates.length > DESKTOP_PET_PROGRAM_LIBRARY_MAX_CANDIDATES) {
        throw new DesktopPetProgramLibraryValidationError(
          'The selected Desktop Pet folder contains too many programs.',
        )
      }
      continue
    }
    if (childDirectories.length > 0 && current.depth >= DESKTOP_PET_PROGRAM_LIBRARY_MAX_DEPTH) {
      throw new DesktopPetProgramLibraryValidationError(
        'The selected Desktop Pet folder is nested too deeply.',
      )
    }
    childDirectories.sort(compareCodePoints).reverse()
    for (const childDirectory of childDirectories) {
      pending.push({
        directory: childDirectory,
        depth: current.depth + 1,
      })
    }
  }
  return candidates.sort((left, right) => (
    compareCodePoints(left.relativeDirectory, right.relativeDirectory)
  ))
}

/**
 * Scan one absolute user-selected folder for the reviewed Bongo Cat build.
 *
 * Invalid launcher-shaped folders are omitted when another valid program is
 * present. If all detected candidates fail validation, the scan fails closed
 * so Settings can distinguish a damaged pack from an ordinary empty folder.
 */
export async function scanDesktopPetProgramLibrary(
  requestedRoot: string,
): Promise<DesktopPetProgramLibraryScan> {
  if (!path.isAbsolute(requestedRoot)) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet program folder must be an absolute path.',
    )
  }
  let rootMetadata
  try {
    rootMetadata = await lstat(requestedRoot)
  } catch {
    throw new DesktopPetProgramLibraryUnavailableError(
      'The selected Desktop Pet program folder is unavailable.',
    )
  }
  if (rootMetadata.isSymbolicLink() || !rootMetadata.isDirectory()) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet program folder must be a real directory.',
    )
  }
  let rootPath: string
  try {
    rootPath = await realpath(requestedRoot)
  } catch {
    throw new DesktopPetProgramLibraryUnavailableError(
      'The selected Desktop Pet program folder is unavailable.',
    )
  }
  if (!isSameNativePath(rootPath, path.resolve(requestedRoot))) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet program folder must not be redirected.',
    )
  }
  const candidates = await collectCandidatePrograms(rootPath)
  const programs: DesktopPetProgramDescriptor[] = []
  for (const candidate of candidates) {
    try {
      programs.push(await validateCandidate(candidate, rootPath))
    } catch (error) {
      if (!(error instanceof DesktopPetProgramLibraryValidationError)) {
        throw error
      }
    }
  }
  if (candidates.length > 0 && programs.length === 0) {
    throw new DesktopPetProgramLibraryValidationError(
      'No trusted, complete Desktop Pet program was found in the selected folder.',
    )
  }
  const ids = new Set(programs.map((program) => program.id))
  if (ids.size !== programs.length) {
    throw new DesktopPetProgramLibraryValidationError(
      'The selected folder contains ambiguous Desktop Pet program identities.',
    )
  }
  return Object.freeze({
    rootPath,
    folderName: safeDisplayName(path.basename(rootPath)),
    programs: Object.freeze(programs),
  })
}

/**
 * Revalidate one selected program immediately before Main launches it.
 *
 * A prior scan is only a snapshot: removable storage or another local process
 * can replace files between selection and launch. This boundary verifies that
 * the root and descriptor still name the same canonical locations, then
 * repeats every structural check and every pinned executable/resource hash.
 * Mutable ``config.json`` remains user-owned and may change when it continues
 * to satisfy the bounded schema.
 */
export async function revalidateDesktopPetProgramForLaunch(
  scan: DesktopPetProgramLibraryScan,
  selectedProgram: DesktopPetProgramDescriptor,
): Promise<DesktopPetProgramDescriptor> {
  if (!path.isAbsolute(scan.rootPath)) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet program root is no longer valid.',
    )
  }
  let rootMetadata
  try {
    rootMetadata = await lstat(scan.rootPath)
  } catch {
    throw new DesktopPetProgramLibraryUnavailableError(
      'The selected Desktop Pet program folder is unavailable.',
    )
  }
  if (rootMetadata.isSymbolicLink() || !rootMetadata.isDirectory()) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet program root is no longer a real directory.',
    )
  }
  let canonicalRoot: string
  try {
    canonicalRoot = await realpath(scan.rootPath)
  } catch {
    throw new DesktopPetProgramLibraryUnavailableError(
      'The selected Desktop Pet program folder is unavailable.',
    )
  }
  if (!isSameNativePath(canonicalRoot, scan.rootPath)) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet program root changed after it was scanned.',
    )
  }
  const scannedProgram = scan.programs.find((program) => (
    program.id === selectedProgram.id
  ))
  if (
    scannedProgram === undefined
    || scannedProgram.displayName !== selectedProgram.displayName
    || !isSameNativePath(
      scannedProgram.programDirectory,
      selectedProgram.programDirectory,
    )
    || !isSameNativePath(
      scannedProgram.executablePath,
      selectedProgram.executablePath,
    )
  ) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet program selection does not belong to this scan.',
    )
  }
  if (
    !path.isAbsolute(selectedProgram.programDirectory)
    || !path.isAbsolute(selectedProgram.executablePath)
    || !isWithin(canonicalRoot, selectedProgram.programDirectory)
  ) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet program selection leaves its selected folder.',
    )
  }
  const directoryName = path.basename(selectedProgram.programDirectory)
  const launcherName = `A${directoryName}.exe`
  const expectedExecutable = path.join(
    selectedProgram.programDirectory,
    launcherName,
  )
  if (!isSameNativePath(expectedExecutable, selectedProgram.executablePath)) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet launcher path changed after it was scanned.',
    )
  }
  const relativeDirectory = normalizedRelativePath(
    canonicalRoot,
    selectedProgram.programDirectory,
  )
  const revalidated = await validateCandidate(Object.freeze({
    programDirectory: selectedProgram.programDirectory,
    launcherName,
    relativeDirectory,
  }), canonicalRoot)
  if (
    revalidated.id !== selectedProgram.id
    || revalidated.displayName !== selectedProgram.displayName
    || !isSameNativePath(
      revalidated.programDirectory,
      selectedProgram.programDirectory,
    )
    || !isSameNativePath(
      revalidated.executablePath,
      selectedProgram.executablePath,
    )
  ) {
    throw new DesktopPetProgramLibraryValidationError(
      'Desktop Pet program identity changed after it was scanned.',
    )
  }
  return revalidated
}

/** Convert a private program scan to the bounded state safe for Settings. */
export function presentDesktopPetProgramLibrary(
  scan: DesktopPetProgramLibraryScan,
): DesktopPetLibraryPresentation {
  return Object.freeze({
    status: scan.programs.length === 0 ? 'empty' : 'ready',
    folderName: scan.folderName,
    models: Object.freeze(scan.programs.map((program) => Object.freeze({
      id: program.id,
      displayName: program.displayName,
    }))),
  })
}

/** Find one selected private descriptor without accepting a native path. */
export function findDesktopPetProgram(
  scan: DesktopPetProgramLibraryScan,
  programIdValue: string,
): DesktopPetProgramDescriptor | null {
  return scan.programs.find((program) => program.id === programIdValue) ?? null
}
