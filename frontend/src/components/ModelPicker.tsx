import { useEffect, useRef, useState } from "react";
import {
  fetchModels,
  switchModel,
  type ModelCatalog,
  type ModelEntry,
  type ProviderGroup,
} from "../api";
import { useLang } from "../i18n";

interface Props {
  current: string;
  onChange: (model: string, fastModel: string) => void;
  online: boolean;
}

const PROVIDER_DOT: Record<ProviderGroup["provider"], string> = {
  ollama:       "var(--moss)",
  anthropic:    "var(--tomato)",
  openai:       "#0a84ff",
  google_genai: "var(--amber)",
};

/** Topbar pill that doubles as a model switcher.
 *  - Click → opens a dropdown listing every provider group.
 *  - Local (Ollama) models are discovered live; hosted providers show
 *    their catalogue with a `requires API key` hint when missing. */
export default function ModelPicker({ current, onChange, online }: Props) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyPick, setBusyPick] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Click-outside to close
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const loadCatalog = async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await fetchModels();
      setCatalog(c);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const toggleOpen = () => {
    if (!online) return;
    const next = !open;
    setOpen(next);
    if (next && !catalog) loadCatalog();
  };

  const pick = async (m: ModelEntry) => {
    if (m.id === current) {
      setOpen(false);
      return;
    }
    if (!m.supports_tools) {
      // We can't put a tool-less model in the big slot — the executor /
      // sub-agents call bind_tools() and will crash. Backend also enforces
      // this with a 422; UI just gives a clearer pre-flight message.
      setError(t("modelPickerNoToolsErr").replace("{name}", m.label));
      return;
    }
    setBusyPick(m.id);
    try {
      const r = await switchModel(m.id);
      onChange(r.model, r.fast_model);
      setCatalog((prev) => (prev ? { ...prev, current: r.model, current_fast: r.fast_model } : prev));
      setError(null);
      setOpen(false);
    } catch (e) {
      setError((e as Error).message || String(e));
    } finally {
      setBusyPick(null);
    }
  };

  return (
    <div ref={rootRef} className="model-picker">
      <button
        type="button"
        className="model-tag picker-trigger"
        onClick={toggleOpen}
        disabled={!online}
        title={online ? t("modelPickerHint") : t("backendOff")}
      >
        <span className="dot-pulse" /> {current}
        <span className="picker-caret" aria-hidden>▾</span>
      </button>

      {open && (
        <div className="picker-menu" role="menu">
          <div className="picker-head">
            <span>{t("modelPickerTitle")}</span>
            <button className="picker-refresh" onClick={loadCatalog} disabled={loading}>
              {loading ? t("modelPickerLoading") : t("modelPickerRefresh")}
            </button>
          </div>

          {error && <div className="picker-error">{error}</div>}

          {!catalog && !error && (
            <div className="picker-empty">{t("modelPickerLoading")}</div>
          )}

          {catalog?.groups.map((g) => (
            <div key={g.provider} className={`picker-group ${g.available ? "" : "off"}`}>
              <div className="picker-group-head">
                <span className="picker-dot" style={{ background: PROVIDER_DOT[g.provider] }} />
                <span className="picker-group-label">{g.label}</span>
                {!g.available && <span className="picker-group-reason">{g.reason}</span>}
              </div>
              <ul className="picker-list">
                {g.models.length === 0 && (
                  <li className="picker-item-empty">{t("modelPickerNoModels")}</li>
                )}
                {g.models.map((m) => {
                  const active = m.id === current;
                  const disabled =
                    !g.available || busyPick !== null || !m.supports_tools;
                  const meta =
                    m.size_gb != null ? `${m.size_gb} GB` : m.tier === "fast" ? "fast" : "";
                  return (
                    <li key={m.id}>
                      <button
                        type="button"
                        className={`picker-item ${active ? "active" : ""} ${
                          !m.supports_tools ? "no-tools" : ""
                        }`}
                        onClick={() => pick(m)}
                        disabled={disabled}
                        title={
                          !m.supports_tools
                            ? t("modelPickerNoToolsHint")
                            : undefined
                        }
                      >
                        <span className="picker-item-label">{m.label}</span>
                        {meta && <span className="picker-item-meta">{meta}</span>}
                        {!m.supports_tools && (
                          <span className="picker-item-badge">{t("modelPickerNoToolsBadge")}</span>
                        )}
                        {active && <span className="picker-check">✓</span>}
                        {busyPick === m.id && <span className="picker-spin">…</span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}

          <div className="picker-foot">{t("modelPickerFoot")}</div>
        </div>
      )}
    </div>
  );
}
