/**
 * @fileoverview Remove only the desktop renderer and Electron build outputs.
 *
 * Build cleanup is intentionally limited to fixed directories derived from
 * this script's own location. Callers choose a named scope instead of passing
 * a filesystem path, so a malformed command cannot expand the deletion
 * boundary beyond `desktop/dist` and `desktop/dist-electron`.
 */

import { rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const outputNamesByScope = Object.freeze({
  renderer: Object.freeze(['dist']),
  electron: Object.freeze(['dist-electron']),
  all: Object.freeze(['dist', 'dist-electron']),
})

/**
 * Resolve one named cleanup scope to its fixed desktop build directories.
 *
 * @param {'renderer' | 'electron' | 'all'} scope cleanup boundary to select.
 * @returns {string[]} absolute, validated output directory paths.
 */
export function resolveBuildOutputTargets(scope) {
  const outputNames = outputNamesByScope[scope]
  if (outputNames === undefined) {
    throw new TypeError(
      `Unknown build cleanup scope ${JSON.stringify(scope)}; `
        + 'expected renderer, electron, or all.',
    )
  }

  return outputNames.map((outputName) => {
    const target = path.resolve(desktopRoot, outputName)
    const relative = path.relative(desktopRoot, target)
    if (relative !== outputName || path.dirname(target) !== desktopRoot) {
      // The fixed-child assertion is deliberately redundant with path.join:
      // it keeps future edits from turning this maintenance helper into an
      // arbitrary recursive deletion primitive.
      throw new Error(`Refusing unsafe build cleanup target: ${target}`)
    }
    return target
  })
}

/**
 * Remove the selected build outputs without accepting caller-provided paths.
 *
 * @param {'renderer' | 'electron' | 'all'} scope cleanup boundary to remove.
 * @returns {Promise<string[]>} the absolute directories considered for removal.
 */
export async function cleanBuildOutputs(scope) {
  const targets = resolveBuildOutputTargets(scope)
  await Promise.all(
    targets.map((target) => rm(target, { recursive: true, force: true })),
  )
  return targets
}

const invokedPath = process.argv[1]
if (
  invokedPath !== undefined
  && path.resolve(invokedPath) === fileURLToPath(import.meta.url)
) {
  const requestedScopes = process.argv.slice(2)
  if (requestedScopes.length !== 1) {
    throw new TypeError(
      'Usage: node scripts/clean-build-output.mjs <renderer|electron|all>',
    )
  }
  const removedTargets = await cleanBuildOutputs(requestedScopes[0])
  console.log(`Cleaned ${removedTargets.map((target) => path.basename(target)).join(', ')}.`)
}
