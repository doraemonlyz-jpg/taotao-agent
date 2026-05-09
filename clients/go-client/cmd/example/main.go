// Example: 4 ways to call the taotao-agent from a Go service.
//
// Usage:
//
//	# 1. Make sure the agent backend is running:
//	cd backend && uvicorn app:app --reload
//
//	# 2. Run any of the demos:
//	go run ./cmd/example/                   # default: stream demo
//	go run ./cmd/example/ -mode=block
//	go run ./cmd/example/ -mode=multi-turn
//	go run ./cmd/example/ -mode=http-server
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/doraemonlyz-jpg/taotao-agent/clients/go-client/agent"
)

func main() {
	mode := flag.String("mode", "stream", "stream | block | multi-turn | http-server")
	base := flag.String("base", envOr("AGENT_URL", "http://localhost:8000"), "agent base URL")
	msg := flag.String("msg", "用一句话介绍一下 LangGraph", "message to send")
	flag.Parse()

	cli := agent.New(*base,
		agent.WithAPIKey(os.Getenv("AGENT_API_KEY")), // optional · empty = no auth header
		agent.WithRetries(2),
	)

	ctx, cancel := signalCtx()
	defer cancel()

	if h, err := cli.Health(ctx); err != nil {
		log.Fatalf("health failed · is the backend up? %v", err)
	} else {
		fmt.Printf("✅ agent up · model=%s · critic=%v · guardrails=%v\n\n",
			h.Model, h.CriticEnabled, h.GuardrailsEnabled)
	}

	switch *mode {
	case "stream":
		runStream(ctx, cli, *msg)
	case "block":
		runBlock(ctx, cli, *msg)
	case "multi-turn":
		runMultiTurn(ctx, cli)
	case "http-server":
		runHTTPServer(ctx, cli)
	default:
		log.Fatalf("unknown mode %q", *mode)
	}
}

// ---------- 1. streaming · print tokens as they arrive ----------

func runStream(ctx context.Context, cli *agent.Client, msg string) {
	fmt.Printf("→ %s\n← ", msg)
	err := cli.ChatStream(ctx, msg, "", agent.ChatHandlerFunc{
		Token: func(t string) { fmt.Print(t) },
		Trace: func(ev agent.TraceEvent) {
			// uncomment to see internal events:
			// fmt.Fprintf(os.Stderr, "[%s.%s] %v\n", ev.Node, ev.Kind, ev.Payload)
			_ = ev
		},
		Session: func(id string) {
			fmt.Fprintf(os.Stderr, "(session=%s)\n", id)
		},
		Done: func() { fmt.Println() },
	})
	if err != nil {
		log.Fatal(err)
	}
}

// ---------- 2. blocking · just give me the final string ----------

func runBlock(ctx context.Context, cli *agent.Client, msg string) {
	res, err := cli.Chat(ctx, msg, "")
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("→ %s\n\n← %s\n\n[session=%s]\n", msg, res.Reply, res.SessionID)
}

// ---------- 3. multi-turn · same session_id keeps conversation memory ----------

func runMultiTurn(ctx context.Context, cli *agent.Client) {
	turns := []string{
		"我叫桃桃，我喜欢小狗",
		"刚才我说我喜欢什么？",
	}
	var sid string
	for i, q := range turns {
		fmt.Printf("\n--- turn %d ---\n→ %s\n", i+1, q)
		res, err := cli.Chat(ctx, q, sid)
		if err != nil {
			log.Fatal(err)
		}
		sid = res.SessionID // reuse next turn
		fmt.Printf("← %s\n", res.Reply)
	}
	fmt.Printf("\n(final session_id=%s)\n", sid)
}

// ---------- 4. expose the agent as your own HTTP API · proxying SSE through ----------

func runHTTPServer(ctx context.Context, cli *agent.Client) {
	mux := http.NewServeMux()

	// POST /api/ask  {"message":"...","session_id":"..."}
	// → returns plain text reply (blocking)
	mux.HandleFunc("/api/ask", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", 405)
			return
		}
		// give each request a 60s budget
		ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
		defer cancel()

		// for brevity: read raw text body as the message
		buf := make([]byte, 4096)
		n, _ := r.Body.Read(buf)
		res, err := cli.Chat(ctx, string(buf[:n]), r.Header.Get("X-Session-ID"))
		if err != nil {
			http.Error(w, err.Error(), 502)
			return
		}
		w.Header().Set("X-Session-ID", res.SessionID)
		w.Write([]byte(res.Reply))
	})

	// GET /api/stream?msg=...&session=...   → SSE pass-through
	mux.HandleFunc("/api/stream", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		flusher, ok := w.(http.Flusher)
		if !ok {
			http.Error(w, "no streaming", 500)
			return
		}

		msg := r.URL.Query().Get("msg")
		sid := r.URL.Query().Get("session")
		err := cli.ChatStream(r.Context(), msg, sid, agent.ChatHandlerFunc{
			Token: func(t string) {
				fmt.Fprintf(w, "data: %s\n\n", t)
				flusher.Flush()
			},
			Done: func() {
				fmt.Fprintf(w, "event: done\ndata: ok\n\n")
				flusher.Flush()
			},
		})
		if err != nil {
			fmt.Fprintf(w, "event: error\ndata: %s\n\n", err)
			flusher.Flush()
		}
	})

	addr := envOr("LISTEN_ADDR", ":8080")
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	fmt.Printf("🚀 listening on %s · POST /api/ask · GET /api/stream?msg=...\n", addr)

	go func() {
		<-ctx.Done()
		_ = srv.Shutdown(context.Background())
	}()
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}

// ---------- helpers ----------

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func signalCtx() (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(context.Background())
	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt, syscall.SIGTERM)
	go func() { <-c; cancel() }()
	return ctx, cancel
}
