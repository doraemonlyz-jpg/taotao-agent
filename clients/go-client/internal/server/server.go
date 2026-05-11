// Package server implements the AgentService Connect-Go handler.
//
// One server, three protocols: when mounted via agentv1connect.NewAgentServiceHandler,
// it serves gRPC, gRPC-Web, and Connect/JSON simultaneously on the same path.
//
// Internally, every RPC just delegates to the existing agent.Client SDK — so all
// the auth/retry/SSE logic stays in ONE place.
package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"connectrpc.com/connect"

	"github.com/doraemonlyz-jpg/taotao-agent/clients/go-client/agent"
	agentv1 "github.com/doraemonlyz-jpg/taotao-agent/clients/go-client/proto/taotao/agent/v1"
	"github.com/doraemonlyz-jpg/taotao-agent/clients/go-client/proto/taotao/agent/v1/agentv1connect"
)

// Server implements agentv1connect.AgentServiceHandler.
//
// It owns NO state of its own — it's just a thin protocol adapter on top
// of the agent.Client REST/SSE SDK. Safe for concurrent use.
type Server struct {
	cli *agent.Client
}

// Compile-time check: Server satisfies the generated Connect-Go interface.
var _ agentv1connect.AgentServiceHandler = (*Server)(nil)

// New returns a Server that talks to the agent backend via cli.
func New(cli *agent.Client) *Server {
	return &Server{cli: cli}
}

// ─────────────────────────────────────────────── Health (unary)

func (s *Server) Health(
	ctx context.Context,
	req *connect.Request[agentv1.HealthRequest],
) (*connect.Response[agentv1.HealthResponse], error) {
	h, err := s.cli.Health(ctx)
	if err != nil {
		return nil, toConnectError(err)
	}
	return connect.NewResponse(&agentv1.HealthResponse{
		Ok:                h.OK,
		Model:             h.Model,
		CriticEnabled:     h.CriticEnabled,
		GuardrailsEnabled: h.GuardrailsEnabled,
	}), nil
}

// ─────────────────────────────────────────────── Chat (unary)

func (s *Server) Chat(
	ctx context.Context,
	req *connect.Request[agentv1.ChatRequest],
) (*connect.Response[agentv1.ChatResponse], error) {
	if req.Msg.GetMessage() == "" {
		return nil, connect.NewError(connect.CodeInvalidArgument, errors.New("message is required"))
	}
	res, err := s.cli.Chat(ctx, req.Msg.GetMessage(), req.Msg.GetSessionId())
	if err != nil {
		return nil, toConnectError(err)
	}
	return connect.NewResponse(&agentv1.ChatResponse{
		SessionId: res.SessionID,
		Reply:     res.Reply,
	}), nil
}

// ─────────────────────────────────────────────── ChatStream (server-streaming)

// ChatStream pumps the SSE event stream from the upstream agent into
// Connect's typed server-stream.
//
// One event maps 1:1 across protocols:
//
//	SSE             →  ChatStreamResponse.kind
//	────────────────   ─────────────────────────
//	"session"       →  KIND_SESSION  + SessionPayload
//	"token"         →  KIND_TOKEN    + TokenPayload
//	"trace"         →  KIND_TRACE    + TracePayload
//	"done"          →  KIND_DONE     + DonePayload
//
// The send happens inside the SDK's callback — when stream.Send returns an
// error (client disconnected) we cancel the SDK call by returning that error
// up the stack, which closes the underlying SSE connection.
func (s *Server) ChatStream(
	ctx context.Context,
	req *connect.Request[agentv1.ChatStreamRequest],
	stream *connect.ServerStream[agentv1.ChatStreamResponse],
) error {
	if req.Msg.GetMessage() == "" {
		return connect.NewError(connect.CodeInvalidArgument, errors.New("message is required"))
	}

	// Bubble any stream.Send error out as a sentinel · the SDK callback can't
	// itself return an error, so we use a captured var + ctx cancellation
	// to tear down cleanly when the consumer disconnects.
	var sendErr error
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	send := func(ev *agentv1.ChatStreamResponse) {
		if sendErr != nil {
			return // already failed · ignore the rest
		}
		if err := stream.Send(ev); err != nil {
			sendErr = err
			cancel() // tear down the upstream SSE call
		}
	}

	err := s.cli.ChatStream(ctx, req.Msg.GetMessage(), req.Msg.GetSessionId(), agent.ChatHandlerFunc{
		Session: func(id string) {
			send(&agentv1.ChatStreamResponse{
				Kind: agentv1.ChatStreamResponse_KIND_SESSION,
				Payload: &agentv1.ChatStreamResponse_Session{
					Session: &agentv1.SessionPayload{SessionId: id},
				},
			})
		},
		Token: func(t string) {
			send(&agentv1.ChatStreamResponse{
				Kind: agentv1.ChatStreamResponse_KIND_TOKEN,
				Payload: &agentv1.ChatStreamResponse_Token{
					Token: &agentv1.TokenPayload{Text: t},
				},
			})
		},
		Trace: func(ev agent.TraceEvent) {
			// Re-encode the opaque payload to JSON · keeps the proto small
			// + lets clients decode only when they care.
			pj, _ := json.Marshal(ev.Payload)
			send(&agentv1.ChatStreamResponse{
				Kind: agentv1.ChatStreamResponse_KIND_TRACE,
				Payload: &agentv1.ChatStreamResponse_Trace{
					Trace: &agentv1.TracePayload{
						Ts:          ev.TS,
						Node:        ev.Node,
						Kind:        ev.Kind,
						PayloadJson: string(pj),
					},
				},
			})
		},
		Done: func() {
			send(&agentv1.ChatStreamResponse{
				Kind: agentv1.ChatStreamResponse_KIND_DONE,
				Payload: &agentv1.ChatStreamResponse_Done{
					Done: &agentv1.DonePayload{},
				},
			})
		},
	})

	// Prefer the send error (consumer-side problem) over the SDK error
	// (upstream problem) — same as gRPC's recommended priority.
	if sendErr != nil {
		return sendErr
	}
	if err != nil {
		// ctx.Canceled on a clean consumer disconnect isn't an error · suppress.
		if errors.Is(err, context.Canceled) && ctx.Err() != nil {
			return nil
		}
		return toConnectError(err)
	}
	return nil
}

// ─────────────────────────────────────────────── error mapping

// toConnectError lifts agent SDK errors into proper Connect-RPC codes so
// gRPC/gRPC-Web clients see the right status (and HTTP/JSON clients see
// the right HTTP code).
func toConnectError(err error) error {
	var apiErr *agent.APIError
	if errors.As(err, &apiErr) {
		switch {
		case apiErr.Status == 400 || apiErr.Status == 422:
			return connect.NewError(connect.CodeInvalidArgument, apiErr)
		case apiErr.Status == 401:
			return connect.NewError(connect.CodeUnauthenticated, apiErr)
		case apiErr.Status == 403:
			return connect.NewError(connect.CodePermissionDenied, apiErr)
		case apiErr.Status == 404:
			return connect.NewError(connect.CodeNotFound, apiErr)
		case apiErr.Status == 429:
			return connect.NewError(connect.CodeResourceExhausted, apiErr)
		case apiErr.Status >= 500:
			return connect.NewError(connect.CodeUnavailable, apiErr)
		}
	}
	if strings.Contains(err.Error(), "context") {
		return connect.NewError(connect.CodeCanceled, err)
	}
	return connect.NewError(connect.CodeInternal, fmt.Errorf("agent: %w", err))
}
