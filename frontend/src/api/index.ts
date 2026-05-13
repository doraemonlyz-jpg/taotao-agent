/**
 * Public surface of the API client.
 *
 * - `schema.gen.ts` is auto-generated from the backend's OpenAPI dump.
 *   Regenerate after any backend change to request/response shapes:
 *
 *       cd frontend && npm run gen:api
 *
 *   That command also writes `frontend/openapi.json` (gitignored) as a
 *   side-effect.  Don't edit `schema.gen.ts` by hand · it'll be clobbered.
 *
 * - Generated types are exposed via the `Schemas` namespace below so call
 *   sites can read e.g. `Schemas["ChatIn"]` instead of digging into the
 *   nested `components["schemas"]` shape.
 *
 * - The runtime fetch wrappers + SSE helper still live in `../api.ts` for
 *   now; this file only re-exports types.
 */
export type { components, operations, paths } from "./schema.gen";

import type { components } from "./schema.gen";

/** Convenient flat alias for the request/response Pydantic models. */
export type Schemas = components["schemas"];
