/**
 * Resolve safe native-directory-picker defaults for external Desktop Pet packs.
 *
 * Native paths remain inside Electron Main.  The renderer can ask Main to open
 * a picker, but it never receives the selected absolute path.
 */

import { stat } from 'node:fs/promises'
import path from 'node:path'

type DirectoryProbe = (candidate: string) => Promise<boolean>

async function isExistingDirectory(candidate: string): Promise<boolean> {
  try {
    return (await stat(candidate)).isDirectory()
  } catch {
    // A disconnected drive or removed folder is expected for an optional pet.
    return false
  }
}

/**
 * Choose the first existing Main-private directory suitable as picker default.
 *
 * A saved user choice wins. Development checkouts may then open the generic
 * character-data parent so the owner can explicitly choose a Git-ignored local
 * pack without publishing its name. Packaged builds never probe beside the
 * installed application for undeclared assets.
 */
export async function resolveDesktopPetPickerDefaultPath(
  savedLibraryPath: string | null,
  projectRoot: string,
  packaged: boolean,
  probe: DirectoryProbe = isExistingDirectory,
): Promise<string | undefined> {
  const candidates = savedLibraryPath === null ? [] : [savedLibraryPath]
  if (!packaged) {
    candidates.push(path.join(
      projectRoot,
      'data',
      'characters',
    ))
  }
  for (const candidate of candidates) {
    try {
      if (await probe(candidate)) {
        return candidate
      }
    } catch {
      // Custom/native probes fail closed so the picker can use its OS default.
    }
  }
  return undefined
}

/** Minimal result shape returned by Electron's native directory picker. */
export interface DesktopPetDirectoryPickerResult {
  canceled: boolean
  filePaths: string[]
}

/**
 * Extract one absolute directory selection or return null for cancellation.
 *
 * Electron normally guarantees this shape.  Rechecking it keeps malformed
 * native or test responses from becoming scanner input.
 */
export function selectedDesktopPetDirectory(
  result: DesktopPetDirectoryPickerResult,
): string | null {
  if (result.canceled) {
    return null
  }
  if (result.filePaths.length !== 1 || !path.isAbsolute(result.filePaths[0]!)) {
    throw new TypeError('The native picker did not return one absolute directory.')
  }
  return path.resolve(result.filePaths[0]!)
}
