/** @fileoverview Resolve the bundled interpreter before any development override. */

import { existsSync } from 'node:fs'
import path from 'node:path'

/** Resolve a relocatable packaged runtime or the explicitly selected development Python. */
export function resolveBackendPython(projectRoot: string): string {
  const bundled = path.join(projectRoot, 'python', 'python.exe')
  // An installed application must not accidentally run a developer shell's
  // Python or import its packages. The staging layout is the package marker.
  if (existsSync(path.join(projectRoot, 'distribution-manifest.json'))) return bundled
  const configured = process.env.ELYSIA_PYTHON?.trim()
  if (configured) return path.resolve(configured)
  return path.join(projectRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
}
