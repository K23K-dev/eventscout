import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    rolldownOptions: {
      output: {
        // Libraries get their own long-cached chunks, so the app's chunk stays small.
        codeSplitting: {
          groups: [
            {
              name: 'react',
              priority: 30,
              test: /node_modules[\\/](react|react-dom|scheduler|react-router|react-router-dom)[\\/]/,
            },
            {
              name: 'ui',
              priority: 20,
              test: /node_modules[\\/](radix-ui|@radix-ui|@floating-ui|react-day-picker|date-fns|sonner|lucide-react)[\\/]/,
            },
            { name: 'vendor', priority: 10, test: /node_modules[\\/]/ },
          ],
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
})
