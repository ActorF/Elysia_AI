/** Bundle the call window's isolated preload without granting the main Desktop API. */

import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const desktopRoot = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  build: {
    copyPublicDir: false,
    emptyOutDir: false,
    lib: {
      entry: path.join(desktopRoot, 'electron', 'voice-call-preload.cts'),
      fileName: () => 'voice-call-preload.cjs',
      formats: ['cjs'],
    },
    minify: false,
    outDir: path.join(desktopRoot, 'dist-electron'),
    rollupOptions: {
      // Sandboxed preloads can resolve Electron, so all closed local validators
      // are bundled into this file instead of relying on relative require().
      external: ['electron'],
      output: { codeSplitting: false, exports: 'auto' },
    },
    sourcemap: true,
    target: 'node22',
  },
})
