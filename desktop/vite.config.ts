/** Build the main application and isolated Voice Call renderers for Electron. */

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
      // A separate HTML entry keeps the call renderer's narrow preload bridge
      // independent from the main application's audio and Backend ownership.
      input: {
        main: path.join(desktopRoot, 'index.html'),
        voiceCall: path.join(desktopRoot, 'voice-call.html'),
      },
    },
  },
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
})
