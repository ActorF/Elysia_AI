/** Configure Vite's React renderer build with paths that also work inside Electron. */

import path from 'node:path'
import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const desktopRoot = path.dirname(fileURLToPath(import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  // Relative assets allow Electron to load the production UI from app.asar.
  base: './',
  build: {
    rollupOptions: {
      input: path.join(desktopRoot, 'index.html'),
    },
  },
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
})
