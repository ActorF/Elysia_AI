import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  // Relative assets allow Electron to load the production UI from app.asar.
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
})
