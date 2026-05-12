import { useLang } from "../i18n";
import type { Engine } from "../api";

interface Props {
  engine: Engine;
  onChange: (next: Engine) => void;
}

/**
 * Segmented-control style switch that swaps the chat backend between
 *   - graph   (POST /chat       · 13-node LangGraph)         · forest green
 *   - harness (POST /chat/v2    · while-loop · Claude Code)  · sunset orange
 *
 * Active segment is filled with the engine's signature color + reverse
 * text. No emoji on the inactive side · the fill IS the indicator. The
 * `engine-${engine}` class on the wrapper drives which segment fills.
 *
 * Both endpoints share SSE wire format · only control flow differs.
 */
export default function EngineToggle({ engine, onChange }: Props) {
  const { t } = useLang();

  return (
    <div
      className={`engine-seg engine-${engine}`}
      role="radiogroup"
      aria-label={t("engineToggleA11y")}
    >
      <span className="engine-seg-label">{t("engineLabel")}</span>
      <div className="engine-seg-track">
        <button
          type="button"
          role="radio"
          aria-checked={engine === "graph"}
          className={`engine-seg-opt ${engine === "graph" ? "on" : ""}`}
          onClick={() => engine !== "graph" && onChange("graph")}
          title={t("engineGraphTip")}
        >
          <span className="engine-seg-icon" aria-hidden>◉</span>
          {t("engineGraph")}
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={engine === "harness"}
          className={`engine-seg-opt ${engine === "harness" ? "on" : ""}`}
          onClick={() => engine !== "harness" && onChange("harness")}
          title={t("engineHarnessTip")}
        >
          <span className="engine-seg-icon" aria-hidden>◐</span>
          {t("engineHarness")}
        </button>
      </div>
    </div>
  );
}
