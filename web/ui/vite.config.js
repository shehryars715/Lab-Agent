import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Two ways to run this, and they use the same code:
//
//   npm run dev    Vite on :5173, proxying /api to the FastAPI server on :8000.
//                  Same-origin from the browser's point of view, so no CORS
//                  middleware is needed anywhere. Hot reload while editing.
//
//   npm run build  Writes dist/. FastAPI mounts that directory at "/" if it
//                  exists, so the whole app is then one process on :8000.
//
// The proxy is not just a convenience -- it is why `EventSource('/api/...')`
// can be a relative URL in both modes. An absolute URL would need CORS for the
// dev case, and a cross-origin EventSource is a different thing to debug.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
