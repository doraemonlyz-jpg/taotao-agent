package agent

import (
	"bufio"
	"io"
	"strings"
)

// sseEvent is one Server-Sent Event message.
//
// SSE wire format (one event per blank-line-separated block):
//
//	event: token
//	data: {"text":"hi"}
//
//	event: done
//	data: {"session_id":"abc"}
type sseEvent struct {
	Event string
	Data  string
}

// sseDecoder reads SSE events from an io.Reader. Not safe for concurrent use.
type sseDecoder struct {
	br *bufio.Reader
}

func newSSEDecoder(r io.Reader) *sseDecoder {
	// 1 MiB buffer · trace events with big payloads (tool output) can be large
	br := bufio.NewReaderSize(r, 1<<20)
	return &sseDecoder{br: br}
}

// next blocks until one full event is read or returns io.EOF.
//
// Multi-line `data:` is concatenated with "\n" per the SSE spec.
// Lines starting with `:` are comments (heartbeats from sse-starlette) — skipped.
// Unknown fields are ignored.
func (d *sseDecoder) next() (sseEvent, error) {
	var ev sseEvent
	var data strings.Builder
	hasContent := false

	for {
		line, err := d.br.ReadString('\n')
		if err != nil {
			// flush whatever we accumulated · EOF mid-event = upstream closed
			if hasContent {
				if data.Len() > 0 {
					ev.Data = data.String()
				}
				return ev, nil
			}
			return ev, err
		}
		// trim trailing \r\n or \n
		line = strings.TrimRight(line, "\r\n")

		// blank line = event boundary
		if line == "" {
			if hasContent {
				if data.Len() > 0 {
					ev.Data = data.String()
				}
				return ev, nil
			}
			continue // ignore leading blank lines
		}

		// comment / heartbeat (sse-starlette sends ": ping\n\n")
		if strings.HasPrefix(line, ":") {
			continue
		}

		// field: value · or just `field` with empty value
		var field, value string
		if i := strings.IndexByte(line, ':'); i >= 0 {
			field = line[:i]
			value = line[i+1:]
			if strings.HasPrefix(value, " ") {
				value = value[1:] // SSE spec: strip ONE leading space
			}
		} else {
			field = line
		}

		switch field {
		case "event":
			ev.Event = value
			hasContent = true
		case "data":
			if data.Len() > 0 {
				data.WriteByte('\n')
			}
			data.WriteString(value)
			hasContent = true
		case "id", "retry":
			// supported but not exposed · we don't need replay
		default:
			// silently ignore unknown fields
		}
	}
}
