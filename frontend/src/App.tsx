import { useEffect, useState } from "react";
import {
  fetchHealth,
  fetchMemory,
  fetchTools,
  type Engine,
  type MemoryItem,
  type ToolDesc,
} from "./api";
import ChatPanel from "./components/ChatPanel";
import TracePanel from "./components/TracePanel";
import Sidebar from "./components/Sidebar";
import TokenMeter from "./components/TokenMeter";
import LangToggle from "./components/LangToggle";
import EngineToggle from "./components/EngineToggle";
import ModelPicker from "./components/ModelPicker";
import type { TraceEvent } from "./api";
import { useLang } from "./i18n";

const ENGINE_KEY = "agent-demo-engine";

function initialEngine(): Engine {
  if (typeof window === "undefined") return "graph";
  const v = window.localStorage.getItem(ENGINE_KEY);
  return v === "harness" ? "harness" : "graph";
}

export default function App() {
  const { t } = useLang();
  const [model, setModel] = useState<string>(t("modelLoading"));
  const [modelOnline, setModelOnline] = useState(true);
  const [tools, setTools] = useState<ToolDesc[]>([]);
  const [memory, setMemory] = useState<MemoryItem[]>([]);
  const [traces, setTraces] = useState<TraceEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turnTick, setTurnTick] = useState(0);
  const [engine, setEngine] = useState<Engine>(initialEngine);

  /**
   * Engines have INDEPENDENT session stores (graph: SQLite checkpointer ·
   * harness: JSON-per-session in data/harness/). Carrying a session_id
   * across a switch would break tool_call/result pairing on the new side ·
   * so we explicitly drop session + traces on every flip.
   */
  const switchEngine = (next: Engine) => {
    if (next === engine) return;
    setEngine(next);
    setSessionId(null);
    setTraces([]);
    window.localStorage.setItem(ENGINE_KEY, next);
  };

  useEffect(() => {
    fetchHealth()
      .then((h) => {
        setModel(h.model);
        setModelOnline(true);
      })
      .catch(() => {
        setModelOnline(false);
      });
    fetchTools().then(setTools).catch(() => setTools([]));
    fetchMemory().then(setMemory).catch(() => setMemory([]));
  }, []);

  // when language changes, re-render the model placeholder if the backend is offline / still loading
  useEffect(() => {
    if (!modelOnline) setModel(t("backendOff"));
  }, [t, modelOnline]);

  const refreshMemory = () => fetchMemory().then(setMemory).catch(() => {});

  const onTurnComplete = () => {
    refreshMemory();
    setTurnTick((n) => n + 1);
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          {t("brandPrefix")}<span className="dot">.</span>{t("brandSuffix")}
        </div>
        <ModelPicker
          current={model}
          online={modelOnline}
          onChange={(m) => setModel(m)}
        />
        <TokenMeter sessionId={sessionId} refreshTick={turnTick} />
        <EngineToggle engine={engine} onChange={switchEngine} />
        <LangToggle />
        <a
          className="repo-link"
          href={import.meta.env.VITE_DOCS_URL ?? "/api/tutorial/index.html"}
          target="_blank"
          rel="noopener noreferrer"
          title={t("tutorialTip")}
        >
          {t("tutorialLink")}
        </a>
        <a
          className="repo-link"
          href="https://github.com/doraemonlyz-jpg/agent-demo"
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("githubLink")}
        </a>
      </header>

      <main className="layout">
        <Sidebar
          tools={tools}
          memory={memory}
          onMemoryChange={refreshMemory}
          refreshTick={turnTick}
          sessionId={sessionId}
          onSwitchSession={(sid) => {
            setSessionId(sid);
            // Clear traces so the user sees a clean slate when they pick
            // a different session · the existing trace events belong to
            // whichever session was just open.
            setTraces([]);
          }}
        />

        <section className="center">
          <ChatPanel
            busy={busy}
            setBusy={setBusy}
            onTraces={setTraces}
            onTurnComplete={onTurnComplete}
            sessionId={sessionId}
            setSessionId={setSessionId}
            engine={engine}
          />
        </section>

        <TracePanel events={traces} busy={busy} />
      </main>
    </div>
  );
}
