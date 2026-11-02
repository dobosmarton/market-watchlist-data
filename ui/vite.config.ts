import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { defineConfig } from "vitest/config";

// One self-contained HTML file with every script and style inlined: the MCP host loads it into a
// sandboxed iframe that cannot fetch anything. The Python server serves it from src/wl/ui.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: { outDir: "../src/wl/ui", emptyOutDir: true },
  test: { environment: "node" },
});
