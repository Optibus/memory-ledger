/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// The build output is COMMITTED (frontend/dist). serve.py is a stdlib server the
// plugin ships with: a user who installs memory-ledger runs `python3
// scripts/serve.py` and gets the UI with no npm, no node, no install step. The
// only way to keep that promise with a compiled SPA is to ship the compiled
// artifact. `base: "./"` keeps the asset URLs relative so the same dist works
// whether it is served from / or from a sub-path.
export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    // One JS file and one CSS file. A local server with no HTTP/2 and no CDN
    // gains nothing from chunking, and a stable pair of names keeps the
    // committed diff readable when only the app code changed.
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/app.[ext]",
      },
    },
  },
  server: {
    port: 5173,
    // `npm run dev` talks to a real serve.py on 8770, so the dev server is a
    // hot-reloading front end over the actual brain, not a mock.
    proxy: { "/api": { target: "http://127.0.0.1:8770", changeOrigin: true } },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    css: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/main.tsx"],
    },
  },
});
