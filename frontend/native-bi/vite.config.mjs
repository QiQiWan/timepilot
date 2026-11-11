import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import { perspectiveExportContractPlugin } from './src/build-contract.mjs';

// Vite Library Mode always inlines imported assets, even large WASM binaries.
// Use an application-style Rollup JS entry so the Perspective WASM files are
// emitted as independent assets, served by FastAPI without any CDN.
export default defineConfig({
  base: '/assets/native-bi/',
  plugins: [perspectiveExportContractPlugin()],
  publicDir: fileURLToPath(new URL('./public', import.meta.url)),
  build: {
    target: 'esnext',
    outDir: process.env.TIMEPILOT_NATIVE_BI_OUTDIR || '../../src/timepilot/static/native-bi',
    emptyOutDir: true,
    assetsInlineLimit: 0,
    cssCodeSplit: false,
    sourcemap: false,
    manifest: true,
    rollupOptions: {
      // Preserve the browser API exported by src/main.js; without this,
      // Vite's application entry strips functions used by the Python UI.
      preserveEntrySignatures: 'strict',
      input: fileURLToPath(new URL('./src/main.js', import.meta.url)),
      output: {
        entryFileNames: 'perspective-entry.js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: asset => asset.name?.endsWith('.css')
          ? 'perspective-entry.css'
          : 'assets/[name]-[hash][extname]',
      },
    },
  },
});
