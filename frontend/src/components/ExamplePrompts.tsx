import { useMemo, useState } from "react";
import { useLang } from "../i18n";
import {
  CATEGORY_META,
  DEMO_PROMPTS,
  groupByCategory,
  type PromptCategory,
} from "../prompts";

interface Props {
  onPick: (text: string) => void;
  disabled?: boolean;
}

const ALL: "all" = "all";

export default function ExamplePrompts({ onPick, disabled }: Props) {
  const { lang } = useLang();
  const [active, setActive] = useState<PromptCategory | typeof ALL>(ALL);

  const grouped = useMemo(groupByCategory, []);
  const visible = useMemo(
    () => (active === ALL ? DEMO_PROMPTS : grouped[active] ?? []),
    [active, grouped],
  );

  const tabs: Array<{ id: PromptCategory | typeof ALL; label: string; emoji: string }> = [
    { id: ALL, label: lang === "zh" ? "全部" : "All", emoji: "✦" },
    ...(Object.keys(CATEGORY_META) as PromptCategory[]).map((id) => ({
      id,
      label: CATEGORY_META[id].label[lang],
      emoji: CATEGORY_META[id].emoji,
    })),
  ];

  return (
    <div className="ex-prompts">
      <div className="ex-prompts-tabs" role="tablist">
        {tabs.map((tab) => {
          const isActive = active === tab.id;
          const tint =
            tab.id === ALL ? "#ffd166" : CATEGORY_META[tab.id].tint;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              className={`ex-prompts-tab ${isActive ? "is-active" : ""}`}
              onClick={() => setActive(tab.id)}
              style={isActive ? { borderColor: tint, color: tint } : undefined}
            >
              <span className="ex-prompts-tab-emoji" aria-hidden>
                {tab.emoji}
              </span>
              {tab.label}
            </button>
          );
        })}
      </div>

      <ul className="ex-prompts-grid">
        {visible.map((p) => {
          const meta = CATEGORY_META[p.category];
          return (
            <li key={p.id}>
              <button
                type="button"
                className="ex-prompts-card"
                disabled={disabled}
                onClick={() => onPick(p.text[lang])}
                style={{ borderColor: `${meta.tint}55` }}
              >
                <div className="ex-prompts-card-head">
                  <span
                    className="ex-prompts-card-tag"
                    style={{ color: meta.tint, borderColor: `${meta.tint}77` }}
                  >
                    {meta.emoji} {meta.label[lang]}
                  </span>
                  {p.prefer && (
                    <span className="ex-prompts-card-engine">
                      {p.prefer === "graph" ? "graph" : "harness"}
                    </span>
                  )}
                </div>
                <p className="ex-prompts-card-text">{p.text[lang]}</p>
                <p className="ex-prompts-card-hint">{p.hint[lang]}</p>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
