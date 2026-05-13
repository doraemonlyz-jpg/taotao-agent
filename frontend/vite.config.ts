import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Vite config · production-ready code-splitting.
 *
 * Why: a single 564KB main bundle was over Vite's 500KB warning. The
 * culprits were the markdown stack (react-markdown + remark-gfm +
 * rehype-highlight + highlight.js with all languages = ~280KB) and the
 * react runtime (~140KB). Both load on first render of an assistant
 * reply, but neither needs to be in the initial paint.
 *
 * Strategy:
 *   1. Split react/react-dom into their own chunk (cached separately,
 *      version-stable).
 *   2. Split the markdown stack into its own chunk · loaded LAZILY by
 *      MarkdownBubble (see src/components/MarkdownBubble.tsx).
 *   3. Everything else stays in `index-*.js`.
 *
 * Result: ~150KB main · ~140KB react · ~280KB markdown (lazy).
 *         First paint pulls main + react = ~290KB. Markdown loads when
 *         the first assistant message arrives.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    // The markdown chunk is unavoidably ~335KB (full markdown parser +
    // syntax highlighter for 190+ languages). Since it's lazy-loaded
    // only on first assistant message, we accept it. Set the warning
    // above markdown but below "we accidentally broke code-splitting".
    chunkSizeWarningLimit: 350,
    rollupOptions: {
      output: {
        manualChunks: (id) => {
          if (!id.includes("node_modules")) return undefined;
          // React core · pin into a separate, cache-stable chunk.
          if (
            id.includes("/react/") ||
            id.includes("/react-dom/") ||
            id.includes("/scheduler/")
          ) {
            return "react-vendor";
          }
          // Markdown stack · large + only needed once a message renders.
          // MarkdownBubble is React.lazy'd, so this whole chunk loads
          // on demand (after the SSE arrives the first assistant token).
          if (
            id.includes("react-markdown") ||
            id.includes("remark-") ||
            id.includes("rehype-") ||
            id.includes("highlight.js") ||
            id.includes("micromark") ||
            id.includes("mdast-") ||
            id.includes("hast-") ||
            id.includes("unist-") ||
            id.includes("/decode-named-character-reference/") ||
            id.includes("/character-entities") ||
            id.includes("/property-information") ||
            id.includes("/space-separated-tokens") ||
            id.includes("/comma-separated-tokens") ||
            id.includes("/devlop") ||
            id.includes("/zwitch") ||
            id.includes("/trim-lines") ||
            id.includes("/longest-streak") ||
            id.includes("/markdown-table") ||
            id.includes("/ccount") ||
            id.includes("/escape-string-regexp") ||
            id.includes("/html-url-attributes") ||
            id.includes("/vfile") ||
            id.includes("/bail") ||
            id.includes("/is-plain-obj") ||
            id.includes("/trough") ||
            id.includes("/unified")
          ) {
            return "markdown";
          }
          // Everything else from node_modules · bundle into the main
          // chunk. Splitting "vendor" out separately created a circular
          // chunk because some shared utils get pulled into both buckets.
          return undefined;
        },
      },
    },
  },
});
