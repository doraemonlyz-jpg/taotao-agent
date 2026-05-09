package agent

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// fakeSSEHandler streams the SSE script to the client.
func fakeSSEHandler(script string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.WriteHeader(200)
		flusher := w.(http.Flusher)
		for _, chunk := range strings.Split(script, "\n\n") {
			if chunk == "" {
				continue
			}
			w.Write([]byte(chunk + "\n\n"))
			flusher.Flush()
		}
	}
}

func TestChatStreamCollectsTokensAndSession(t *testing.T) {
	script := `event: session
data: {"session_id":"sess-42"}

event: token
data: {"text":"Hello"}

event: token
data: {"text":" world"}

event: trace
data: {"ts":1.0,"node":"executor","kind":"tool_call","payload":{"tool":"calc"}}

event: done
data: {"session_id":"sess-42"}`

	srv := httptest.NewServer(fakeSSEHandler(script))
	defer srv.Close()

	cli := New(srv.URL)
	res, err := cli.Chat(context.Background(), "hi", "")
	if err != nil {
		t.Fatalf("Chat err: %v", err)
	}
	if res.Reply != "Hello world" {
		t.Errorf("reply = %q · want %q", res.Reply, "Hello world")
	}
	if res.SessionID != "sess-42" {
		t.Errorf("session = %q · want sess-42", res.SessionID)
	}
}

func TestChatStreamCallbacks(t *testing.T) {
	script := `event: session
data: {"session_id":"s1"}

event: token
data: {"text":"A"}

event: trace
data: {"ts":1,"node":"planner","kind":"plan","payload":{"step":"s1"}}

event: token
data: {"text":"B"}

event: done
data: {"session_id":"s1"}`

	srv := httptest.NewServer(fakeSSEHandler(script))
	defer srv.Close()

	var tokens, traces, sessions, dones int32
	var collected strings.Builder
	err := New(srv.URL).ChatStream(context.Background(), "x", "", ChatHandlerFunc{
		Token:   func(t string) { atomic.AddInt32(&tokens, 1); collected.WriteString(t) },
		Trace:   func(TraceEvent) { atomic.AddInt32(&traces, 1) },
		Session: func(string) { atomic.AddInt32(&sessions, 1) },
		Done:    func() { atomic.AddInt32(&dones, 1) },
	})
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if tokens != 2 || traces != 1 || sessions != 1 || dones != 1 {
		t.Errorf("counts: tokens=%d traces=%d sessions=%d dones=%d", tokens, traces, sessions, dones)
	}
	if collected.String() != "AB" {
		t.Errorf("collected=%q want AB", collected.String())
	}
}

func TestChatStreamCancelsOnContext(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(200)
		flusher := w.(http.Flusher)
		for i := 0; i < 100; i++ {
			select {
			case <-r.Context().Done():
				return
			default:
			}
			w.Write([]byte("event: token\ndata: {\"text\":\"x\"}\n\n"))
			flusher.Flush()
			time.Sleep(20 * time.Millisecond)
		}
	}))
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Millisecond)
	defer cancel()

	err := New(srv.URL).ChatStream(ctx, "x", "", ChatHandlerFunc{})
	if err == nil {
		t.Fatal("expected ctx error · got nil")
	}
	if !strings.Contains(err.Error(), "context") {
		t.Errorf("expected context err · got %v", err)
	}
}

func TestRetryOn5xx(t *testing.T) {
	var hits int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt32(&hits, 1)
		if n < 3 {
			w.WriteHeader(503)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"ok":true,"model":"m"}`))
	}))
	defer srv.Close()

	cli := New(srv.URL, WithRetries(3))
	h, err := cli.Health(context.Background())
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if !h.OK || hits != 3 {
		t.Errorf("ok=%v hits=%d", h.OK, hits)
	}
}

func TestNoRetryOn4xx(t *testing.T) {
	var hits int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&hits, 1)
		w.WriteHeader(400)
		w.Write([]byte("bad"))
	}))
	defer srv.Close()

	_, err := New(srv.URL, WithRetries(5)).Health(context.Background())
	if err == nil {
		t.Fatal("want err")
	}
	if hits != 1 {
		t.Errorf("4xx must not retry · hits=%d", hits)
	}
}

func TestSSEDecoderIgnoresHeartbeats(t *testing.T) {
	script := `: keepalive

event: token
data: {"text":"hi"}

: another ping

event: done
data: {}`
	srv := httptest.NewServer(fakeSSEHandler(script))
	defer srv.Close()

	res, err := New(srv.URL).Chat(context.Background(), "x", "")
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if res.Reply != "hi" {
		t.Errorf("reply=%q want hi", res.Reply)
	}
}

func TestSSEDecoderHandlesMultilineData(t *testing.T) {
	script := "event: trace\ndata: line1\ndata: line2\n\nevent: done\ndata: {}"
	srv := httptest.NewServer(fakeSSEHandler(script))
	defer srv.Close()

	var traceData string
	err := New(srv.URL).ChatStream(context.Background(), "x", "", ChatHandlerFunc{
		Trace: func(ev TraceEvent) {},
		Done:  func() {},
		Token: func(string) {},
	})
	_ = traceData
	if err != nil {
		t.Fatalf("err: %v", err)
	}
}
