/// <reference types="vite/client" />

// Project-specific env vars exposed to the client at build time.
// Add new VITE_* keys here so TypeScript knows about them.
interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_DOCS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
