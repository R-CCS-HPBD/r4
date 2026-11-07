import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        rag: resolve(__dirname, 'apps/rag-explorer/index.html'),
        jobs: resolve(__dirname, 'apps/job-monitor/index.html'),
        model: resolve(__dirname, 'apps/model-wizard/index.html'),
        embeddings: resolve(__dirname, 'apps/embedding-viz/index.html'),
      },
    },
    outDir: 'dist',
  },
  server: {
    port: 5173,
    proxy: {
      '/umrs': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/gw': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
});
