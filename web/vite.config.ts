/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'
import { umamiPlugin } from './umami.ts'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // VITE_* values from web/.env* files or the environment (Cloudflare Pages settings).
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  return {
    plugins: [react(), umamiPlugin(env)],
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
    },
  }
})
