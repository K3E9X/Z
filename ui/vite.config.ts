import { fileURLToPath, URL } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// The API is same-origin in every deployment (nginx in z-ui proxies /api to z-server).
// In development Vite proxies /api to a local z-server (Z_API_URL, default http://127.0.0.1:8000).
const apiTarget = process.env.Z_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false, ws: false },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: false,
    // No inline <script> in index.html (CSP script-src 'self'): Vite emits external module scripts only.
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 900,
  },
})
