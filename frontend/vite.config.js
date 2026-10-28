import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The Vue dev server (5173) proxies /api → the FastAPI backend. This keeps the
// browser talking to a single origin, so session cookies work with no CORS pain
// — and it is why reaching the app from a phone needs no CORS change either:
// the phone loads the page from this server and its /api calls go to the same
// address, which Vite forwards on.
//
// VAM_HOST comes from the launcher: unset (or 127.0.0.1) means this PC only,
// which is the default. start-lan.bat sets 0.0.0.0 to let other devices on the
// network in. Keeping it out of the committed default means the exposed state
// is always something someone chose.
const host = process.env.VAM_HOST || '127.0.0.1'

export default defineConfig({
  plugins: [vue()],
  server: {
    host,
    port: 5173,
    // A phone on the LAN loads the page over http://192.168.x.x:5173, so the
    // HMR socket has to dial back to that same address rather than localhost.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8301', changeOrigin: true },
    },
  },
})
