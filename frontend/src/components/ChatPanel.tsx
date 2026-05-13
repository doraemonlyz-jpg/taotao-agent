import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { streamChat, type Engine, type TraceEvent } from "../api";
import { pick, samples, useLang } from "../i18n";

// Lazy · the markdown stack is ~280KB (react-markdown + remark-gfm +
// rehype-highlight + highlight.js). Loading it on demand keeps the
// initial paint snappy. The fallback is a plain text render so a slow
// network user still sees the answer immediately.
const MarkdownBubble = lazy(() => import("./MarkdownBubble"));

function PlainText({ text }: { text: string }) {
  return <div className="md md-fallback">{text}</div>;
}

interface Turn {
  role: "user" | "assistant" | "system";
  text: string;
  meta?: string;
}

interface Props {
  busy: boolean;
  setBusy: (b: boolean) => void;
  onTraces: (
    next: TraceEvent[] | ((prev: TraceEvent[]) => TraceEvent[])
  ) => void;
  onTurnComplete: () => void;
  sessionId: string | null;
  setSessionId: (sid: string) => void;
  engine: Engine;
}

export default function ChatPanel({
  busy,
  setBusy,
  onTraces,
  onTurnComplete,
  sessionId,
  setSessionId,
  engine,
}: Props) {
  const { lang, t } = useLang();
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState("");
  const abortRef = useRef<null | (() => void)>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollerRef.current?.scrollTo({
      top: scrollerRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [turns, streaming]);

  const send = () => {
    const msg = input.trim();
    if (!msg || busy) return;

    setInput("");
    setBusy(true);
    setStreaming("");
    setTurns((t) => [...t, { role: "user", text: msg }]);
    onTraces([]); // clear trace panel for the new turn

    let lastAnswer = "";
    const streamedChunks: string[] = [];
    let toolNotes: string[] = [];

    abortRef.current = streamChat(
      msg,
      sessionId,
      {
        onSession: (sid) => setSessionId(sid),
        onToken: (text) => {
          streamedChunks.push(text);
          setStreaming((cur) => cur + text);
        },
        onTrace: (evt) => {
          onTraces((prev) => [...prev, evt]);

          if (evt.kind === "tool_call") {
            const calls = (evt.payload.calls ?? []) as Array<{ name: string }>;
            for (const c of calls) {
              if (c.name) toolNotes.push(c.name);
            }
          } else if (
            evt.kind === "answer" &&
            typeof evt.payload.text === "string"
          ) {
            lastAnswer = evt.payload.text as string;
          } else if (
            evt.kind === "subagent" &&
            typeof evt.payload.out === "string"
          ) {
            lastAnswer = evt.payload.out as string;
          }
        },
        onDone: () => {
          setBusy(false);
          const finalText =
            lastAnswer || streamedChunks.join("") || t("chatNoAnswer");
          const meta = toolNotes.length
            ? `${t("chatTools")}: ${[...new Set(toolNotes)].join(", ")}`
            : undefined;
          setStreaming("");
          setTurns((prev) => [
            ...prev,
            { role: "assistant", text: finalText, meta },
          ]);
          onTurnComplete();
        },
        onError: (e) => {
          setBusy(false);
          setStreaming("");
          setTurns((prev) => [
            ...prev,
            { role: "system", text: `${t("chatError")}: ${e}` },
          ]);
        },
      },
      engine
    );
  };

  const cancel = () => {
    abortRef.current?.();
    abortRef.current = null;
    setBusy(false);
  };

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scrollerRef}>
        {turns.length === 0 && (
          <div className="empty">
            <p className="hello">{t("chatHello")}</p>
            <ul className="samples">
              {samples.map((s) => {
                const text = pick(s, lang);
                return (
                  <li key={text}>
                    <button
                      type="button"
                      onClick={() => setInput(text)}
                      disabled={busy}
                    >
                      {text}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {turns.map((turn, i) => (
          <div key={i} className={`bubble bubble-${turn.role}`}>
            <div className="bubble-role">
              {turn.role === "user"
                ? t("roleYou")
                : turn.role === "assistant"
                ? t("roleAgent")
                : t("roleSystem")}
            </div>
            <div className="bubble-text">
              {turn.role === "user" ? (
                // User text is plain — never render their `**` as bold.
                turn.text
              ) : (
                <Suspense fallback={<PlainText text={turn.text} />}>
                  <MarkdownBubble text={turn.text} />
                </Suspense>
              )}
            </div>
            {turn.meta && <div className="bubble-meta">{turn.meta}</div>}
          </div>
        ))}

        {busy && (
          <div className="bubble bubble-assistant streaming">
            <div className="bubble-role">{t("roleAgent")}</div>
            {streaming ? (
              <div className="bubble-text">
                <Suspense fallback={<PlainText text={streaming} />}>
                  <MarkdownBubble text={streaming} />
                </Suspense>
                <span className="caret">▋</span>
              </div>
            ) : (
              <div className="thinking">
                <span /> <span /> <span />
              </div>
            )}
          </div>
        )}
      </div>

      <div className="composer">
        <textarea
          rows={2}
          value={input}
          placeholder={t("chatPlaceholder")}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          disabled={busy}
        />
        {busy ? (
          <button className="btn-stop" type="button" onClick={cancel}>
            {t("chatStop")}
          </button>
        ) : (
          <button className="btn-send" type="button" onClick={send}>
            {t("chatSend")}
          </button>
        )}
      </div>
    </div>
  );
}
