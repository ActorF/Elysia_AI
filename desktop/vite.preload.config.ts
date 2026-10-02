/** Bundle the sandboxed Electron Preload into one self-contained CommonJS file. */

import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vite'

const desktopRoot = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  build: {
    copyPublicDir: false,
    emptyOutDir: false,
    lib: {
      entry: path.join(desktopRoot, 'electron', 'preload.cts'),
      fileName: () => 'preload.cjs',
      formats: ['cjs'],
    },
    minify: false,
    outDir: path.join(desktopRoot, 'dist-electron'),
    rollupOptions: {
      // Electron is the only runtime capability exposed by the sandbox. Every
      // local helper must be bundled because sandboxed Preloads cannot resolve
      // arbitrary relative CommonJS modules.
      external: ['electron'],
      output: {
        codeSplitting: false,
        exports: 'auto',
      },
    },
    sourcemap: true,
    target: 'node22',
  },
})
