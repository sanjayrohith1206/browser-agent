import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

const root = import.meta.dirname;

// Copies manifest.json into the build output.
function manifest(): Plugin {
  return {
    name: 'extension-manifest',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'manifest.json',
        source: readFileSync(resolve(root, 'manifest.json'), 'utf8'),
      });
    },
  };
}

// Side panel + background service worker. The content script is built
// separately (vite.content.config.ts) because it must be a classic script.
export default defineConfig(({ mode }) => ({
  plugins: [react(), manifest()],
  resolve: { alias: { '@shared': resolve(root, '../shared') } },
  build: {
    outDir: 'dist',
    // In watch mode the content-script watcher shares dist/, so don't wipe it.
    emptyOutDir: mode === 'production',
    target: 'chrome116',
    sourcemap: true,
    rollupOptions: {
      input: {
        sidepanel: resolve(root, 'sidepanel.html'),
        background: resolve(root, 'src/background/index.ts'),
      },
      output: {
        entryFileNames: (chunk) =>
          chunk.name === 'background' ? 'background.js' : 'assets/[name]-[hash].js',
      },
    },
  },
}));
