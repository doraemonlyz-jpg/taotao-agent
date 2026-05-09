import { useEffect, useState } from "react";
import {
  clearMemory,
  clearProfile,
  clearReflections,
  deleteProfileKey,
  fetchProfile,
  fetchReflections,
  fetchSkills,
  type MemoryItem,
  type SkillEntry,
  type ToolDesc,
} from "../api";
import { components, pick, useLang } from "../i18n";

interface Props {
  tools: ToolDesc[];
  memory: MemoryItem[];
  onMemoryChange: () => void;
  /** Bumped each time a turn completes, so memory sub-panes refetch. */
  refreshTick: number;
}

type Tab = "arch" | "tools" | "mem";
type MemSub = "facts" | "reflections" | "profile" | "skills";

export default function Sidebar({ tools, memory, onMemoryChange, refreshTick }: Props) {
  const { lang, t } = useLang();
  const [tab, setTab] = useState<Tab>("arch");
  const [memSub, setMemSub] = useState<MemSub>("facts");

  const [profile, setProfile] = useState<Record<string, unknown>>({});
  const [reflections, setReflections] = useState<MemoryItem[]>([]);
  const [skills, setSkills] = useState<SkillEntry[]>([]);
  const [openSkill, setOpenSkill] = useState<string | null>(null);

  useEffect(() => {
    fetchSkills().then(setSkills).catch(() => setSkills([]));
  }, []);
  useEffect(() => {
    fetchProfile().then(setProfile).catch(() => setProfile({}));
    fetchReflections().then(setReflections).catch(() => setReflections([]));
  }, [refreshTick]);

  const refreshAllMem = () => {
    onMemoryChange();
    fetchProfile().then(setProfile).catch(() => {});
    fetchReflections().then(setReflections).catch(() => {});
  };

  return (
    <aside className="side">
      <div className="side-tabs">
        <button className={tab === "arch" ? "on" : ""} onClick={() => setTab("arch")}>
          {t("tabComponents")}
        </button>
        <button className={tab === "tools" ? "on" : ""} onClick={() => setTab("tools")}>
          {t("tabTools")} <span className="count">{tools.length}</span>
        </button>
        <button className={tab === "mem" ? "on" : ""} onClick={() => setTab("mem")}>
          {t("tabMemory")}{" "}
          <span className="count">
            {memory.length + reflections.length + Object.keys(profile).length + skills.length}
          </span>
        </button>
      </div>

      <div className="side-body">
        {tab === "arch" && (
          <ul className="arch-list">
            {components.map((c) => (
              <li key={pick(c.label, "en")}>
                <div className="arch-num">{c.num}</div>
                <div>
                  <div className="arch-label">{pick(c.label, lang)}</div>
                  <div className="arch-note">{pick(c.note, lang)}</div>
                </div>
              </li>
            ))}
          </ul>
        )}

        {tab === "tools" && (
          <ul className="tool-list">
            {tools.length === 0 && <li className="muted">{t("sideOffline")}</li>}
            {tools.map((tool) => (
              <li key={tool.name}>
                <code>{tool.name}</code>
                <p>{tool.description}</p>
              </li>
            ))}
          </ul>
        )}

        {tab === "mem" && (
          <div className="mem-pane">
            <div className="mem-subtabs">
              <button className={memSub === "facts" ? "on" : ""} onClick={() => setMemSub("facts")}>
                {t("memSubFacts")} <span className="count">{memory.length}</span>
              </button>
              <button className={memSub === "reflections" ? "on" : ""} onClick={() => setMemSub("reflections")}>
                {t("memSubReflections")} <span className="count">{reflections.length}</span>
              </button>
              <button className={memSub === "profile" ? "on" : ""} onClick={() => setMemSub("profile")}>
                {t("memSubProfile")} <span className="count">{Object.keys(profile).length}</span>
              </button>
              <button className={memSub === "skills" ? "on" : ""} onClick={() => setMemSub("skills")}>
                {t("memSubSkills")} <span className="count">{skills.length}</span>
              </button>
            </div>

            {memSub === "facts" && (
              <>
                <div className="mem-actions">
                  <button
                    className="mem-clear"
                    onClick={async () => {
                      if (confirm(t("memClearConfirm"))) {
                        await clearMemory();
                        refreshAllMem();
                      }
                    }}
                    disabled={memory.length === 0}
                  >
                    {t("memClearAll")}
                  </button>
                </div>
                <ul className="mem-list">
                  {memory.length === 0 && <li className="muted">{t("memEmpty")}</li>}
                  {memory.map((m) => (
                    <li key={m.id}>
                      <div className="mem-text">{m.text}</div>
                      <div className="mem-meta">
                        {(m.metadata as { kind?: string }).kind ?? "fact"} ·{" "}
                        {(m.metadata as { ts?: string }).ts?.slice(0, 19) ?? ""}
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}

            {memSub === "reflections" && (
              <>
                <div className="mem-actions">
                  <button
                    className="mem-clear"
                    onClick={async () => {
                      if (confirm(t("reflClearConfirm"))) {
                        await clearReflections();
                        refreshAllMem();
                      }
                    }}
                    disabled={reflections.length === 0}
                  >
                    {t("memClearAll")}
                  </button>
                </div>
                <ul className="mem-list">
                  {reflections.length === 0 && <li className="muted">{t("reflEmpty")}</li>}
                  {reflections.map((m) => (
                    <li key={m.id}>
                      <div className="mem-text">{m.text}</div>
                      <div className="mem-meta">
                        {(m.metadata as { source?: string }).source ?? "—"} ·{" "}
                        {(m.metadata as { ts?: string }).ts?.slice(0, 19) ?? ""}
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}

            {memSub === "profile" && (
              <>
                <div className="mem-actions">
                  <button
                    className="mem-clear"
                    onClick={async () => {
                      if (confirm(t("profClearConfirm"))) {
                        await clearProfile();
                        refreshAllMem();
                      }
                    }}
                    disabled={Object.keys(profile).length === 0}
                  >
                    {t("memClearAll")}
                  </button>
                </div>
                <ul className="profile-list">
                  {Object.keys(profile).length === 0 && (
                    <li className="muted">{t("profEmpty")}</li>
                  )}
                  {Object.entries(profile).map(([k, v]) => (
                    <li key={k}>
                      <div className="profile-key">{k}</div>
                      <div className="profile-val">
                        {typeof v === "string" ? v : JSON.stringify(v)}
                      </div>
                      <button
                        className="profile-x"
                        title={t("profDeleteKey")}
                        onClick={async () => {
                          await deleteProfileKey(k);
                          refreshAllMem();
                        }}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}

            {memSub === "skills" && (
              <ul className="skill-list">
                {skills.length === 0 && <li className="muted">{t("skillEmpty")}</li>}
                {skills.map((s) => {
                  const open = openSkill === s.name;
                  return (
                    <li key={s.name} className={open ? "open" : ""}>
                      <button
                        className="skill-head"
                        onClick={() => setOpenSkill(open ? null : s.name)}
                      >
                        <span className="skill-caret">{open ? "▾" : "▸"}</span>
                        <code>{s.name}</code>
                        <span className="skill-desc">{s.description}</span>
                      </button>
                      {open && (
                        <pre className="skill-body">{s.body}</pre>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
