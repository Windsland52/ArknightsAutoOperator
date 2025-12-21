import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  root: 'frontend',
  plugins: [vue()],
  server: {
    port: 34115,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
