import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: env.VITE_DEV_API_URL
        ? { '/experiments': { target: env.VITE_DEV_API_URL, changeOrigin: true } }
        : undefined,
    },
  }
})
