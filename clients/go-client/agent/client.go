// Package agent is a tiny Go SDK for the taotao-agent FastAPI backend.
//
// Two ways to call the agent:
//
//   1. cli.Chat(ctx, "hello")             // blocking · returns the full reply
//   2. cli.ChatStream(ctx, "hello", h)    // streaming · h.OnToken called per chunk
//
// Both reuse the same /chat SSE endpoint — Chat just collects tokens internally.
package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Client is safe for concurrent use across goroutines.
type Client struct {
	baseURL string
	http    *http.Client
	apiKey  string // optional · sent as Bearer if non-empty
	retries int    // retry count for non-streaming POSTs · default 2
}

// Option configures the Client.
type Option func(*Client)

// WithHTTPClient lets you inject a custom *http.Client (timeouts, transports,
// circuit breakers, tracing, etc).
func WithHTTPClient(c *http.Client) Option { return func(o *Client) { o.http = c } }

// WithAPIKey adds Authorization: Bearer <key> to every request.
func WithAPIKey(k string) Option { return func(o *Client) { o.apiKey = k } }

// WithRetries sets retry count for non-streaming requests (default 2).
// Streaming /chat is NEVER retried automatically — the caller decides.
func WithRetries(n int) Option { return func(o *Client) { o.retries = n } }

// New returns a Client that talks to baseURL (e.g. "http://localhost:8000").
//
// Default *http.Client has NO total timeout (because /chat streams), but ships
// with a 5s connect timeout + 30s response-header timeout via a custom
// Transport — long enough for the supervisor to think, short enough to detect
// a dead backend.
func New(baseURL string, opts ...Option) *Client {
	c := &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		retries: 2,
		http: &http.Client{
			// no Timeout · SSE may stream for minutes
			Transport: &http.Transport{
				ResponseHeaderTimeout: 30 * time.Second,
				IdleConnTimeout:       90 * time.Second,
				MaxIdleConnsPerHost:   8,
			},
		},
	}
	for _, o := range opts {
		o(c)
	}
	return c
}

// ---------------------------------------------------------------- shared helpers

func (c *Client) newReq(ctx context.Context, method, path string, body any) (*http.Request, error) {
	var rdr io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("marshal: %w", err)
		}
		rdr = bytes.NewReader(buf)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, rdr)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}
	return req, nil
}

// doJSON performs the request and decodes JSON into out (if non-nil).
// Retries up to c.retries on 5xx + network errors with exponential backoff.
// Never retries on 4xx (client error) or non-idempotent POSTs unless retryOK.
func (c *Client) doJSON(ctx context.Context, req *http.Request, out any, retryOK bool) error {
	maxAttempts := 1
	if retryOK {
		maxAttempts = c.retries + 1
	}
	var lastErr error
	for attempt := 0; attempt < maxAttempts; attempt++ {
		if attempt > 0 {
			delay := time.Duration(200*(1<<uint(attempt-1))) * time.Millisecond
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(delay):
			}
			// rebuild request body since it's already drained
			if req.GetBody != nil {
				body, err := req.GetBody()
				if err != nil {
					return err
				}
				req.Body = body
			}
		}
		resp, err := c.http.Do(req)
		if err != nil {
			lastErr = err
			continue
		}
		// 5xx → retry · 4xx → fail fast · 2xx → decode
		if resp.StatusCode >= 500 {
			lastErr = fmt.Errorf("server %d", resp.StatusCode)
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			continue
		}
		if resp.StatusCode >= 400 {
			b, _ := io.ReadAll(resp.Body)
			resp.Body.Close()
			return &APIError{Status: resp.StatusCode, Body: string(b)}
		}
		defer resp.Body.Close()
		if out == nil {
			return nil
		}
		return json.NewDecoder(resp.Body).Decode(out)
	}
	return lastErr
}

// APIError is returned for non-2xx responses (after retries exhausted).
type APIError struct {
	Status int
	Body   string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("agent api: status=%d body=%s", e.Status, e.Body)
}

// ---------------------------------------------------------------- /health

type Health struct {
	OK                 bool   `json:"ok"`
	Model              string `json:"model"`
	CriticEnabled      bool   `json:"critic_enabled"`
	GuardrailsEnabled  bool   `json:"guardrails_enabled"`
}

func (c *Client) Health(ctx context.Context) (*Health, error) {
	req, err := c.newReq(ctx, http.MethodGet, "/health", nil)
	if err != nil {
		return nil, err
	}
	var out Health
	if err := c.doJSON(ctx, req, &out, true); err != nil {
		return nil, err
	}
	return &out, nil
}

// ---------------------------------------------------------------- /chat (blocking)

// Chat sends a single message and returns the agent's full reply (concatenated
// from all streamed tokens). Use ChatStream if you want incremental output.
//
// sessionID can be empty (a new one is generated server-side and returned in the
// SSE `session` event — captured in result.SessionID for follow-up turns).
func (c *Client) Chat(ctx context.Context, message, sessionID string) (*ChatResult, error) {
	var sb strings.Builder
	out := &ChatResult{}
	err := c.ChatStream(ctx, message, sessionID, ChatHandlerFunc{
		Token: func(t string) { sb.WriteString(t) },
		Session: func(sid string) { out.SessionID = sid },
		Done:    func() {},
	})
	if err != nil {
		return nil, err
	}
	out.Reply = sb.String()
	return out, nil
}

// ChatResult is what Chat returns once the stream finishes.
type ChatResult struct {
	SessionID string // server-issued (or echoed) — pass back next turn for memory continuity
	Reply     string // full concatenated reply from user-facing nodes
}

// ---------------------------------------------------------------- /chat (streaming)

// ChatHandler receives SSE events as they arrive.
//
// Token   — one chunk of the agent's user-facing reply (executor / writer node)
// Trace   — internal events (planner picks tool, executor calls API, …) · for logging / UI
// Session — fired ONCE at start with the server-side session id
// Done    — fired ONCE when the graph finishes successfully
//
// Any handler may be nil if you don't care about that channel.
type ChatHandler interface {
	OnToken(text string)
	OnTrace(ev TraceEvent)
	OnSession(id string)
	OnDone()
}

// TraceEvent mirrors the trace JSON the backend publishes.
type TraceEvent struct {
	TS      float64                `json:"ts"`
	Node    string                 `json:"node"`
	Kind    string                 `json:"kind"`
	Payload map[string]any         `json:"payload"`
}

// ChatHandlerFunc is a convenience adapter — pass only the callbacks you care
// about, leave the rest nil.
type ChatHandlerFunc struct {
	Token   func(text string)
	Trace   func(ev TraceEvent)
	Session func(id string)
	Done    func()
}

func (h ChatHandlerFunc) OnToken(t string)        { if h.Token != nil { h.Token(t) } }
func (h ChatHandlerFunc) OnTrace(ev TraceEvent)   { if h.Trace != nil { h.Trace(ev) } }
func (h ChatHandlerFunc) OnSession(id string)     { if h.Session != nil { h.Session(id) } }
func (h ChatHandlerFunc) OnDone()                 { if h.Done != nil { h.Done() } }

// ChatStream POSTs to /chat and pushes SSE events to handler as they arrive.
// Blocks until the `done` event or ctx is cancelled.
//
// IMPORTANT: cancelling ctx will close the connection and cause the backend's
// async runner task to be cancelled — safe to call from request handlers that
// time out.
func (c *Client) ChatStream(ctx context.Context, message, sessionID string, handler ChatHandler) error {
	body := map[string]any{"message": message}
	if sessionID != "" {
		body["session_id"] = sessionID
	}
	req, err := c.newReq(ctx, http.MethodPost, "/chat", body)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Cache-Control", "no-cache")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("post /chat: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(resp.Body)
		return &APIError{Status: resp.StatusCode, Body: string(b)}
	}

	dec := newSSEDecoder(resp.Body)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		ev, err := dec.next()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return nil // upstream closed cleanly
			}
			return err
		}
		switch ev.Event {
		case "session":
			var p struct{ SessionID string `json:"session_id"` }
			_ = json.Unmarshal([]byte(ev.Data), &p)
			handler.OnSession(p.SessionID)
		case "token":
			var p struct{ Text string `json:"text"` }
			if err := json.Unmarshal([]byte(ev.Data), &p); err == nil && p.Text != "" {
				handler.OnToken(p.Text)
			}
		case "trace":
			var p TraceEvent
			if err := json.Unmarshal([]byte(ev.Data), &p); err == nil {
				handler.OnTrace(p)
			}
		case "done":
			handler.OnDone()
			return nil
		}
	}
}

// ---------------------------------------------------------------- /memory

type MemoryItem struct {
	ID   string `json:"id"`
	Text string `json:"text"`
	Kind string `json:"kind"`
}

func (c *Client) ListMemory(ctx context.Context) ([]MemoryItem, error) {
	req, err := c.newReq(ctx, http.MethodGet, "/memory", nil)
	if err != nil {
		return nil, err
	}
	var out []MemoryItem
	if err := c.doJSON(ctx, req, &out, true); err != nil {
		return nil, err
	}
	return out, nil
}

func (c *Client) AddMemory(ctx context.Context, text, kind string) (string, error) {
	if kind == "" {
		kind = "fact"
	}
	req, err := c.newReq(ctx, http.MethodPost, "/memory", map[string]string{"text": text, "kind": kind})
	if err != nil {
		return "", err
	}
	var out struct{ ID string `json:"id"` }
	// memory writes are idempotent enough to retry (worst case: dup row · cleaned by extractor)
	if err := c.doJSON(ctx, req, &out, true); err != nil {
		return "", err
	}
	return out.ID, nil
}

func (c *Client) ClearMemory(ctx context.Context) error {
	req, err := c.newReq(ctx, http.MethodDelete, "/memory", nil)
	if err != nil {
		return err
	}
	return c.doJSON(ctx, req, nil, true)
}

// ---------------------------------------------------------------- /profile

func (c *Client) GetProfile(ctx context.Context) (map[string]any, error) {
	req, err := c.newReq(ctx, http.MethodGet, "/profile", nil)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := c.doJSON(ctx, req, &out, true); err != nil {
		return nil, err
	}
	return out, nil
}

func (c *Client) SetProfile(ctx context.Context, key string, value any) (map[string]any, error) {
	req, err := c.newReq(ctx, http.MethodPut, "/profile",
		map[string]any{"key": key, "value": value})
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := c.doJSON(ctx, req, &out, true); err != nil {
		return nil, err
	}
	return out, nil
}

// ---------------------------------------------------------------- /usage

type Usage struct {
	Global  TokenStats             `json:"global"`
	Session map[string]TokenStats  `json:"session,omitempty"`
}

type TokenStats struct {
	PromptTokens     int     `json:"prompt_tokens"`
	CompletionTokens int     `json:"completion_tokens"`
	TotalTokens      int     `json:"total_tokens"`
	USD              float64 `json:"usd"`
}

func (c *Client) Usage(ctx context.Context, sessionID string) (map[string]any, error) {
	q := url.Values{}
	if sessionID != "" {
		q.Set("session_id", sessionID)
	}
	path := "/usage"
	if len(q) > 0 {
		path += "?" + q.Encode()
	}
	req, err := c.newReq(ctx, http.MethodGet, path, nil)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := c.doJSON(ctx, req, &out, true); err != nil {
		return nil, err
	}
	return out, nil
}

// ---------------------------------------------------------------- /models

func (c *Client) ListModels(ctx context.Context) (map[string]any, error) {
	req, err := c.newReq(ctx, http.MethodGet, "/models", nil)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := c.doJSON(ctx, req, &out, true); err != nil {
		return nil, err
	}
	return out, nil
}

func (c *Client) SwitchModel(ctx context.Context, model, fastModel string) error {
	body := map[string]string{}
	if model != "" {
		body["model"] = model
	}
	if fastModel != "" {
		body["fast_model"] = fastModel
	}
	req, err := c.newReq(ctx, http.MethodPost, "/model", body)
	if err != nil {
		return err
	}
	return c.doJSON(ctx, req, nil, false) // not idempotent · don't retry
}
