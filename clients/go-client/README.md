# taotao-agent · Go SDK

A small, production-ready Go client for the taotao-agent FastAPI backend.

> **TL;DR** — `cli.Chat(ctx, "hello", "")` blocks and returns the full reply ·
> `cli.ChatStream(ctx, "hello", "", h)` pipes tokens to your handler ·
> both share the same `/chat` SSE endpoint.

---

## Install

```bash
go get github.com/doraemonlyz-jpg/taotao-agent/clients/go-client/agent
```

> No third-party dependencies — pure stdlib (`net/http` + `bufio` + `encoding/json`).

---

## 30-second example

```go
package main

import (
    "context"
    "fmt"
    "github.com/doraemonlyz-jpg/taotao-agent/clients/go-client/agent"
)

func main() {
    cli := agent.New("http://localhost:8000")

    res, err := cli.Chat(context.Background(), "用一句话介绍 LangGraph", "")
    if err != nil { panic(err) }

    fmt.Println("session:", res.SessionID)
    fmt.Println("reply  :", res.Reply)
}
```

Run the agent first (`cd backend && uvicorn app:app --reload`), then `go run` your file.

---

## 4 calling patterns

The `cmd/example/` binary demonstrates all four:

```bash
go run ./cmd/example/                     # 1. streaming (default)
go run ./cmd/example/ -mode=block         # 2. blocking · single string
go run ./cmd/example/ -mode=multi-turn    # 3. carry session_id across turns
go run ./cmd/example/ -mode=http-server   # 4. expose your own /api/ask + /api/stream
```

### 1 · Streaming (incremental tokens for a UI)

```go
cli.ChatStream(ctx, "解释 actor model", "", agent.ChatHandlerFunc{
    Token:   func(t string) { fmt.Print(t) },              // every chunk
    Trace:   func(ev agent.TraceEvent) { /* logging */ },  // internal events
    Session: func(id string) { /* save for next turn */ },
    Done:    func() { fmt.Println() },
})
```

### 2 · Blocking (just give me the string)

```go
res, _ := cli.Chat(ctx, "你好", "")
fmt.Println(res.Reply, res.SessionID)
```

### 3 · Multi-turn memory

Pass the previous `session_id` back so the agent's checkpointer + memory layer
keep continuity:

```go
r1, _ := cli.Chat(ctx, "我叫桃桃", "")
r2, _ := cli.Chat(ctx, "我叫什么？", r1.SessionID)  // ← remembers
```

### 4 · Wrap as your own HTTP API

`cmd/example/main.go -mode=http-server` shows a 30-line proxy that exposes:

- `POST /api/ask` — blocking, returns `text/plain` (set `X-Session-ID` header)
- `GET  /api/stream?msg=...&session=...` — SSE pass-through to the browser

Drop straight into your existing service.

---

## What you get out of the box

| | |
|---|---|
| **Cancellation** | Pass a `context.Context` — cancel propagates to the upstream SSE connection, backend's async runner task is killed cleanly |
| **Retries** | `WithRetries(3)` — exponential backoff on 5xx + network errors; 4xx fails fast (it's your bug); streaming `/chat` is never auto-retried (caller decides — partial replies must not be hidden) |
| **No timeout on /chat** | LangGraph turns can take minutes (sub-agents + tools); we use a 30s **header** timeout to detect a dead backend, but no total timeout |
| **SSE done right** | Multi-line `data:` concatenated per spec, comments (`:` heartbeats from `sse-starlette`) skipped, 1 MiB buffer for big trace payloads |
| **Auth** | `WithAPIKey("...")` adds `Authorization: Bearer …` to every request |
| **Custom transport** | `WithHTTPClient(yours)` — drop in your own client with mTLS, OpenTelemetry, circuit breaker, etc. |

---

## API surface

```go
cli := agent.New(baseURL string, opts ...Option)

// chat
cli.Chat(ctx, message, sessionID string) (*ChatResult, error)
cli.ChatStream(ctx, message, sessionID string, h ChatHandler) error

// admin
cli.Health(ctx) (*Health, error)
cli.ListModels(ctx) (map[string]any, error)
cli.SwitchModel(ctx, model, fastModel string) error

// memory · profile · usage
cli.ListMemory(ctx) ([]MemoryItem, error)
cli.AddMemory(ctx, text, kind string) (id string, err error)
cli.ClearMemory(ctx) error

cli.GetProfile(ctx) (map[string]any, error)
cli.SetProfile(ctx, key string, value any) (map[string]any, error)

cli.Usage(ctx, sessionID string) (map[string]any, error)
```

### Options

```go
agent.WithHTTPClient(*http.Client)  // custom transport
agent.WithAPIKey(string)            // bearer token
agent.WithRetries(int)              // retry count for non-streaming, default 2
```

---

## Production checklist

When you embed this SDK in a real Go service, do these 5 things:

1. **Keep one `*agent.Client` per process** — it's safe for concurrent use, the
   underlying `http.Transport` pools connections.
2. **Always pass a deadline-bound `ctx`** — wrap incoming request contexts with
   `context.WithTimeout(parent, 60*time.Second)` so a hung backend can't pin
   your goroutines.
3. **Persist `session_id` next to your user record** — not in process memory.
   The agent's `MemorySaver` is keyed on it; lose it = lose memory.
4. **Forward `Trace` events to your logger / Datadog / Langfuse** — the backend
   already publishes everything per node; just plug it in.
5. **Don't auto-retry streaming on errors** — partial replies have already
   spent tokens and may have called tools. Surface the error, let the user
   re-ask. (The SDK enforces this — `ChatStream` does NOT retry.)

---

## Testing

```bash
cd clients/go-client
go test ./agent -count=1 -v
```

7 tests cover: SSE collection, callbacks, ctx cancellation, 5xx retry,
4xx no-retry, heartbeat skip, multi-line `data:`.

---

## File layout

```
clients/go-client/
├── go.mod
├── README.md                ← you are here
├── agent/
│   ├── client.go            ← Client + Chat + ChatStream + Health/Memory/...
│   ├── sse.go               ← stdlib SSE decoder (no third-party deps)
│   └── client_test.go       ← 7 unit tests with httptest.Server
└── cmd/
    └── example/
        └── main.go          ← 4 runnable demos (stream / block / multi-turn / http-server)
```

---

## Want more?

- **gRPC instead of REST?** wrap the SDK in your own `proto` service; no need
  to change the agent backend.
- **Other languages?** the wire format is plain SSE-over-HTTP, so a TypeScript /
  Rust / Python client is the same shape — file a PR if you build one.
- **Auth, rate-limit, audit?** the agent backend doesn't have those — put them
  in your Go service in front. That's the recommended deploy:

  ```
  user → Go service (auth + rate-limit + audit) → taotao-agent (LangGraph)
                                                  ↓
                                            (Anthropic / OpenAI / Ollama)
  ```
