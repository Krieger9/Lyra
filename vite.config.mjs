import { defineConfig } from "vite";

export default defineConfig({
  publicDir: false,
  build: {
    outDir: "static/admin/flow-assets",
    rollupOptions: {
      input: "flow-editor/editor.tsx",
      output: { entryFileNames: "editor.js", assetFileNames: "[name][extname]" },
    },
  },
});
