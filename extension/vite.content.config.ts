import { resolve } from 'node:path';
import { defineConfig } from 'vite';

const root = import.meta.dirname;

// Content scripts injected with chrome.scripting.executeScript must be
// classic scripts, so this entry is bundled as a single IIFE.
export default defineConfig({
  resolve: { alias: { '@shared': resolve(root, '../shared') } },
  build: {
    outDir: 'dist',
    emptyOutDir: false,
    target: 'chrome116',
    sourcemap: true,
    lib: {
      entry: resolve(root, 'src/content/index.ts'),
      formats: ['iife'],
      name: 'BrowserAgentContent',
      fileName: () => 'content.js',
    },
  },
});
