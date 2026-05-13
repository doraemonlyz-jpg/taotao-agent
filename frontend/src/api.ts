/* Tiny fetch wrappers + an SSE chat helper.
 * In dev we go through Vite's `/api` proxy → http://127.0.0.1:8000.
 * In prod the same `/api` prefix is expected.
 *
 * Request-body types come from the auto-generated OpenAPI schema
 * (src/api/schema.gen.ts).  Response types stay hand-written here
 * because most handlers return raw dicts without FastAPI response_model
 * annotations · once those exist we can flip to the generated ones.
 */
import type { Schemas } from "./api/index";
import { authHeaders, clearAuth } from "./auth";

const BASE = "/api";

/* Module-local fetch wrapper · transparently attaches auth headers
 * (X-API-Key or Authorization: Bearer based on the user's pick in
 * LoginPanel) so we don't have to touch every callsite below.
 *
 * 401 → clear stored credentials so AuthGate flips back to login.
 * Errors propagate · callers handle them like before.
 */
const _origFetch = window.fetch.bind(window);
async function fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string>) || {}),
    ...authHeaders(),
  };
  const res = await _origFetch(input, { ...init, headers });
  if (res.status === 401) {
    clearAuth();
  }
  return res;
}

export type ChatIn = Schemas["ChatIn"];
export type MemoryIn = Schemas["MemoryIn"];
export type ModelSwitchIn = Schemas["ModelSwitchIn"];
export type ProfileIn = Schemas["ProfileIn"];
export type PruneIn = Schemas["PruneIn"];
export type ReplayIn = Schemas["ReplayIn"];

export interface TraceEvent {
  ts: number;
  node: string;
  kind: string;
  payload: Record<string, unknown>;
}

export interface ToolDesc {
  name: string;
  description: string;
}

export interface MemoryItem {
  id: string;
  text: string;
  metadata: Record<string, unknown>;
}

export async function fetchTools(): Promise<ToolDesc[]> {
  const r = await fetch(`${BASE}/tools`);
  return r.json();
}

export async function fetchMemory(): Promise<MemoryItem[]> {
  const r = await fetch(`${BASE}/memory`);
  return r.json();
}

export async function clearMemory(): Promise<void> {
  await fetch(`${BASE}/memory`, { method: "DELETE" });
}

export interface PruneResult {
  total: number;
  kept: number;
  dropped: number;
  dry_run: boolean;
  policy?: { max_keep: number; drop_fraction: number; half_life_days: number };
  victim_preview?: string[];
}

export async function pruneMemory(p: Partial<PruneIn> = {}): Promise<PruneResult> {
  const r = await fetch(`${BASE}/memory/prune`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(p),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function fetchHealth(): Promise<{ ok: boolean; model: string }> {
  const r = await fetch(`${BASE}/health`);
  return r.json();
}

/* ---------------- model catalogue + switching ---------------- */
export interface ModelEntry {
  id: string;
  label: string;
  tier: "big" | "fast" | "any";
  size_gb: number | null;
  /** False for known no-tool models (Ollama R1, Gemma, etc.). They can't be
   *  the executor / sub-agent model — only the router / critic / extractor. */
  supports_tools: boolean;
}

export interface ProviderGroup {
  provider: "ollama" | "anthropic" | "openai" | "google_genai";
  label: string;
  available: boolean;
  reason: string | null;
  models: ModelEntry[];
}

export interface ModelCatalog {
  current: string;
  current_fast: string;
  groups: ProviderGroup[];
}

export async function fetchModels(): Promise<ModelCatalog> {
  const r = await fetch(`${BASE}/models`);
  return r.json();
}

export async function switchModel(
  model: string,
  fast_model?: string
): Promise<{ ok: boolean; model: string; fast_model: string }> {
  const r = await fetch(`${BASE}/model`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, fast_model }),
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const j = await r.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return r.json();
}

export interface UsageBucket {
  input: number;
  output: number;
  cache_creation: number;
  cache_read: number;
  calls: number;
  cost_usd: number;
  since?: number;
}

export interface UsageSnapshot {
  model: string;
  pricing_known: boolean;
  budget_usd: number;
  over_budget?: boolean;
  global: UsageBucket;
  session_id?: string;
  session?: UsageBucket;
}

export async function fetchUsage(sessionId: string | null): Promise<UsageSnapshot> {
  const url = sessionId
    ? `${BASE}/usage?session_id=${encodeURIComponent(sessionId)}`
    : `${BASE}/usage`;
  const r = await fetch(url);
  return r.json();
}

/* ---------------- profile ---------------- */
export async function fetchProfile(): Promise<Record<string, unknown>> {
  const r = await fetch(`${BASE}/profile`);
  return r.json();
}

export async function updateProfile(key: string, value: unknown): Promise<Record<string, unknown>> {
  const r = await fetch(`${BASE}/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  });
  return r.json();
}

export async function deleteProfileKey(key: string): Promise<Record<string, unknown>> {
  const r = await fetch(`${BASE}/profile/${encodeURIComponent(key)}`, { method: "DELETE" });
  return r.json();
}

export async function clearProfile(): Promise<{ ok: boolean }> {
  const r = await fetch(`${BASE}/profile`, { method: "DELETE" });
  return r.json();
}

/* ---------------- reflections ---------------- */
export async function fetchReflections(): Promise<MemoryItem[]> {
  const r = await fetch(`${BASE}/reflections`);
  return r.json();
}

export async function clearReflections(): Promise<{ ok: boolean }> {
  const r = await fetch(`${BASE}/reflections`, { method: "DELETE" });
  return r.json();
}

/* ---------------- skills ---------------- */
export interface SkillEntry {
  name: string;
  description: string;
  when_to_use: string;
  body: string;
  path: string;
}

export async function fetchSkills(): Promise<SkillEntry[]> {
  const r = await fetch(`${BASE}/skills`);
  return r.json();
}

/* ---------------- replay / past sessions ---------------- */
export interface SessionSummary {
  session_id: string;
  first_user: string | null;
  last_ts: number;
}

export async function listSessions(limit = 50): Promise<SessionSummary[]> {
  const r = await fetch(`${BASE}/chat/replay/sessions?limit=${limit}`);
  if (!r.ok) return [];
  return r.json();
}

/* ---- SSE chat stream --------------------------------------------------- */

export interface ChatHandlers {
  onSession: (sessionId: string) => void;
  onTrace: (evt: TraceEvent) => void;
  /** Streaming token chunk from a user-facing node (executor / writer). */
  onToken?: (text: string) => void;
  onDone: () => void;
  onError: (err: string) => void;
}

/** Which control-flow implementation to drive · same SSE wire format. */
export type Engine = "graph" | "harness";

/**
 * Posts a chat message and consumes the SSE stream of trace events.
 * Returns a function to abort the in-flight request.
 *
 * `engine`:
 *   - "graph"   → POST /chat       · 13-node LangGraph (default)
 *   - "harness" → POST /chat/v2    · single while-loop · Claude Code style
 *                 see docs/harness.html for the full design walkthrough
 */
export function streamChat(
  message: string,
  sessionId: string | null,
  handlers: ChatHandlers,
  engine: Engine = "graph"
): () => void {
  const ctrl = new AbortController();
  const path = engine === "harness" ? "/chat/v2" : "/chat";

  (async () => {
    try {
      const resp = await fetch(`${BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId }),
        signal: ctrl.signal,
      });
      if (!resp.ok || !resp.body) {
        handlers.onError(`HTTP ${resp.status}`);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let doneFired = false;

      const dispatch = (event: string, data: string) => {
        if (!data) return;
        try {
          const parsed = JSON.parse(data);
          if (event === "session") handlers.onSession(parsed.session_id);
          else if (event === "trace") handlers.onTrace(parsed as TraceEvent);
          else if (event === "token") {
            const text = (parsed as { text?: string }).text;
            if (text && handlers.onToken) handlers.onToken(text);
          } else if (event === "done") {
            doneFired = true;
            handlers.onDone();
          }
        } catch {
          /* ignore malformed frames */
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        // Normalise CRLF → LF; sse-starlette uses CRLF on some setups.
        buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

        // Frames are separated by a blank line: "...\n\n"
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);

          let event = "message";
          const dataLines: string[] = [];
          for (const rawLine of frame.split("\n")) {
            const line = rawLine.replace(/\r$/, "");
            if (line.startsWith(":")) continue;            // SSE comment
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
          }
          dispatch(event, dataLines.join("\n"));
        }
      }
      if (!doneFired) handlers.onDone();
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        handlers.onError(String(e));
      }
    }
  })();

  return () => ctrl.abort();
}
