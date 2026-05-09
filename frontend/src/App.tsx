import { useEffect, useState } from "react";
import {
  fetchHealth,
  fetchMemory,
  fetchTools,
  type MemoryItem,
  type ToolDesc,
} from "./api";
import ChatPanel from "./components/ChatPanel";
import TracePanel from "./components/TracePanel";
import Sidebar from "./components/Sidebar";
import TokenMeter from "./components/TokenMeter";
import LangToggle from "./components/LangToggle";
import ModelPicker from "./components/ModelPicker";
import type { TraceEvent } from "./api";
import { useLang } from "./i18n";

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
        <LangToggle />
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
        />

        <section className="center">
          <ChatPanel
            busy={busy}
            setBusy={setBusy}
            onTraces={setTraces}
            onTurnComplete={onTurnComplete}
            sessionId={sessionId}
            setSessionId={setSessionId}
          />
        </section>

        <TracePanel events={traces} busy={busy} />
      </main>
    </div>
  );
}
