import { useEffect, useRef } from "react";
import type { TraceEvent } from "../api";
import { useLang } from "../i18n";

interface Props {
  events: TraceEvent[];
  busy: boolean;
}

const KIND_COLORS: Record<string, string> = {
  perception:    "#3a5b46",
  guardrail:     "#8a5a3c",
  plan:          "#1c1a14",
  tool_call:     "#d43c26",
  tool_result:   "#7c7363",
  subagent:      "#3a5b46",
  critique:      "#8a5a3c",
  memory_update: "#c97c2a",
  answer:        "#1c1a14",
  error:         "#9b2218",
};

const COMPONENT_LABELS: Record<string, string> = {
  summarizer:        "S  Summarizer",
  perception:        "5  Perception",
  input_guardrail:   "G  Guardrail (in)",
  output_guardrail:  "G  Guardrail (out)",
  guardrail_out:     "G  Guardrail (out)",
  planner:           "4  Planner",
  executor:          "1+6  LLM + Tool exec",
  supervisor:        "O  Orchestrator",
  researcher:        "+  Sub-agent · researcher",
  coder:             "+  Sub-agent · coder",
  writer:            "+  Sub-agent · writer",
  critic:            "R  Critic / Reflection",
  extractor:         "X  Memory extractor",
};

function summarise(evt: TraceEvent): string {
  const p = evt.payload as Record<string, unknown>;
  switch (evt.kind) {
    case "perception":
      if (evt.node === "summarizer")
        return `compacted ${p.compacted ?? 0} msgs · kept ${p.kept ?? 0} · summary ${p.summary_chars ?? 0} chars`;
      return (
        `parsed ${p.chars ?? 0} chars · ` +
        `facts ${p.facts_recalled ?? 0} · reflections ${p.reflections_recalled ?? 0} · ` +
        `profile keys [${(p.profile_keys as string[] | undefined)?.join(", ") ?? ""}]`
      );
    case "memory_update": {
      const facts = (p.facts as string[] | undefined) ?? [];
      const refl = (p.reflections as string[] | undefined) ?? [];
      const prof = Object.keys((p.profile_updates as object | undefined) ?? {});
      return `+${facts.length} facts · +${refl.length} reflections · profile [${prof.join(", ")}]`;
    }
    case "guardrail":
      return `${p.action ?? ""}${p.reason ? " · " + p.reason : ""}`;
    case "plan":
      return `route: ${p.route} · ${(p.plan as string[] | undefined)?.length ?? 0} steps`;
    case "tool_call": {
      const calls = p.calls as Array<{ name: string; args?: Record<string, unknown> }>;
      return calls?.map((c) => `${c.name}(${Object.keys(c.args ?? {}).join(", ")})`).join("  +  ") ?? "";
    }
    case "subagent":
      return `agent: ${p.agent}${p.next ? " · next: " + p.next : ""}`;
    case "critique":
      return `passed=${p.passed} · revisions=${p.revisions ?? 0}${p.notes ? "\n— " + (p.notes as string) : ""}`;
    case "answer":
      return (p.text as string) ?? "";
    case "error":
      return (p.error as string) ?? "(error)";
    default:
      return JSON.stringify(p);
  }
}

export default function TracePanel({ events, busy }: Props) {
  const { t } = useLang();
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scroller.current?.scrollTo({
      top: scroller.current.scrollHeight,
      behavior: "smooth",
    });
  }, [events.length]);

  return (
    <aside className="trace">
      <div className="trace-head">
        <div className="trace-title">{t("traceTitle")}</div>
        <div className={`trace-badge ${busy ? "on" : ""}`}>
          {busy ? t("traceRunning") : t("traceIdle")}
        </div>
      </div>

      <div className="trace-scroll" ref={scroller}>
        {events.length === 0 && (
          <div className="trace-empty">{t("traceEmpty")}</div>
        )}

        {events.map((e, i) => {
          const label = COMPONENT_LABELS[e.node] ?? e.node;
          const color = KIND_COLORS[e.kind] ?? "#7c7363";
          return (
            <div key={i} className="trace-row">
              <div className="trace-node" style={{ color }}>
                <span className="trace-pip" style={{ background: color }} />
                {label}
              </div>
              <div className="trace-kind">{e.kind}</div>
              <div className="trace-body">{summarise(e)}</div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
