import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../dist/webview',
    emptyOutDir: true,
    // The extension host writes the <script>/<link> tags itself, so the
    // generated index.html is unused and the filenames must stay stable.
    rollupOptions: {
      input: resolve(__dirname, 'src/main.tsx'),
      output: {
        entryFileNames: 'assets/index.js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: (info) =>
          info.names?.some((name) => name.endsWith('.css'))
            ? 'assets/index.css'
            : 'assets/[name][extname]',
      },
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    // Dev server port — matches the CSP in agent-view.ts
    port: 5173,
    strictPort: true,
  },
});

