import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Lang = "en" | "zh";

const STORAGE_KEY = "agent-demo-lang";

type Dict = Record<string, { en: string; zh: string }>;

/* ------------------------------------------------------------------ *
 * Static UI strings                                                  *
 * ------------------------------------------------------------------ */
export const ui: Dict = {
  // topbar
  brandPrefix:  { en: "Taotao",                                    zh: "桃桃" },
  brandSuffix:  { en: "Agent",                                     zh: "Agent" },
  modelLoading: { en: "(loading…)",                                zh: "(加载中…)" },
  backendOff:   { en: "(backend offline — start `uvicorn app:app`)", zh: "(后端未启动 — 请运行 `uvicorn app:app`)" },
  githubLink:   { en: "github →",                                  zh: "Github →" },
  tutorialLink: { en: "tutorial →",                                zh: "教程 →" },
  tutorialTip:  { en: "22 books · senior agent engineer interview prep",
                  zh: "22 本书 · senior agent engineer 面试速通" },
  langToggleA11y: { en: "Switch language",                         zh: "切换语言" },

  // engine (graph vs harness) toggle
  engineLabel:    { en: "engine",                                  zh: "架构" },
  engineGraph:    { en: "Graph",                                   zh: "Graph" },
  engineHarness:  { en: "Harness",                                 zh: "Harness" },
  engineToggleA11y: { en: "Switch agent architecture",             zh: "切换 agent 架构" },
  engineGraphTip: {
    en: "POST /chat · 13-node LangGraph · framework decides routing",
    zh: "POST /chat · 13 节点 LangGraph · 框架决定路由",
  },
  engineHarnessTip: {
    en: "POST /chat/v2 · single while-loop · LLM decides via tool_call (Claude Code / Cursor style)",
    zh: "POST /chat/v2 · 单 while-loop · LLM 通过 tool_call 决定（Claude Code / Cursor 风格）",
  },
  engineSwitchedToast: {
    en: "Switched to {engine} · session reset (each engine has its own state store)",
    zh: "已切换到 {engine} · 会话已重置（两种架构存储不同）",
  },

  // model picker
  modelPickerHint:    { en: "Click to switch model",                zh: "点击切换模型" },
  modelPickerTitle:   { en: "Choose a model",                       zh: "选择模型" },
  modelPickerRefresh: { en: "refresh",                              zh: "刷新" },
  modelPickerLoading: { en: "loading…",                             zh: "加载中…" },
  modelPickerNoModels:{ en: "(no models pulled — try `ollama pull qwen2.5:14b`)",
                        zh: "(未拉取模型 — 试试 `ollama pull qwen2.5:14b`)" },
  modelPickerFoot:    { en: "Live switch — no restart needed. Picking a hosted model auto-pairs its cheap fast-tier model.",
                        zh: "热切换，无需重启。选线上模型时会自动配对它的廉价 fast 档位模型。" },
  modelPickerSwitched:{ en: "Switched to",                          zh: "已切换到" },
  modelPickerNoToolsBadge: { en: "no tools",                        zh: "不支持工具" },
  modelPickerNoToolsHint:  {
    en: "This model can't do function calling — fine as a fast/router model, but not as the executor.",
    zh: "此模型不支持 function calling — 可作为路由/批评模型，但不能跑 executor。",
  },
  modelPickerNoToolsErr:   {
    en: "{name} doesn't support tool calling. The executor and sub-agents need it. Pick a different model — or set this one as fast_model only.",
    zh: "{name} 不支持工具调用，executor 和 sub-agent 都依赖它。请选别的模型，或者只把它放在 fast_model 槽。",
  },

  // token meter
  meterThisTurn: { en: "this turn",         zh: "本次会话" },
  meterTotal:    { en: "total",             zh: "累计" },
  meterTipUp:    { en: "input tokens",      zh: "输入 tokens" },
  meterTipDn:    { en: "output tokens",     zh: "输出 tokens" },
  meterTipCache: { en: "cache-read tokens", zh: "缓存命中 tokens" },
  meterTipCost:  { en: "estimated cost (USD)", zh: "估算成本（美元）" },
  meterTipCalls: { en: "LLM calls",         zh: "LLM 调用次数" },
  meterTipNoPricing: {
    en: "Pricing not configured for this model — cost is $0",
    zh: "当前模型未配置定价 — 成本显示为 $0",
  },
  meterBudget:    { en: "budget", zh: "预算" },
  meterTipBudget: {
    en: "Per-session USD cap. Backend refuses new turns once exceeded.",
    zh: "单会话美元上限。超额后后端会拒绝下一轮请求。",
  },

  // chat panel
  chatHello:     { en: "Ask Taotao anything. Watch the agent think on the right →",
                   zh: "随便问桃桃点什么，右侧实时显示 agent 的思考过程 →" },
  chatPlaceholder: { en: "Type a message — Shift+Enter for newline, Enter to send",
                     zh: "输入消息 — Shift+Enter 换行，Enter 发送" },
  chatSend:      { en: "send →",   zh: "发送 →" },
  chatStop:      { en: "stop",     zh: "停止" },
  chatNoAnswer:  { en: "(no answer)", zh: "(无回复)" },
  chatError:     { en: "Error",    zh: "错误" },
  chatTools:     { en: "tools",    zh: "工具" },
  roleYou:       { en: "you",      zh: "你" },
  roleAgent:     { en: "agent",    zh: "agent" },
  roleSystem:    { en: "system",   zh: "系统" },

  // sidebar tabs
  tabComponents: { en: "Components", zh: "组件" },
  tabTools:      { en: "Tools",      zh: "工具" },
  tabMemory:     { en: "Memory",     zh: "记忆" },
  tabSessions:   { en: "Sessions",   zh: "会话" },
  sideOffline:   { en: "(backend offline)", zh: "(后端未启动)" },

  // sessions tab
  sessEmpty:     { en: "(no past sessions yet — chat once and they'll show up)",
                   zh: "(暂无历史会话 — 发送一次消息后会出现)" },
  sessNew:       { en: "+ new", zh: "+ 新对话" },
  sessReplay:    { en: "replay", zh: "回放" },
  sessActive:    { en: "active", zh: "当前" },
  sessNewConfirm:{ en: "Start a fresh session? The current chat will stay in history.",
                   zh: "开启新对话？当前对话会保留在历史里。" },

  // memory sub-tabs
  memSubFacts:       { en: "Facts",       zh: "事实" },
  memSubReflections: { en: "Reflections", zh: "反思" },
  memSubProfile:     { en: "Profile",     zh: "画像" },
  memSubSkills:      { en: "Skills",      zh: "技能" },

  // memory: facts
  memClearAll:   { en: "clear all",  zh: "清空" },
  memClearConfirm: { en: "Clear all long-term facts?", zh: "确定清空全部事实记忆？" },
  memEmpty:      { en: "(no facts yet — chat with the agent and it will start writing)",
                   zh: "(暂无事实记忆 — 跟 agent 聊几句它会自动写入)" },

  // memory: reflections
  reflClearConfirm: { en: "Clear all reflections?", zh: "确定清空全部反思？" },
  reflEmpty:        { en: "(no reflections yet — they accumulate when the critic flags issues)",
                      zh: "(暂无反思 — 当 critic 提出修改意见时会自动累积)" },

  // memory: profile
  profClearConfirm: { en: "Clear the entire profile?", zh: "确定清空整个画像？" },
  profEmpty:        { en: "(profile is empty — say e.g. 'remember I prefer concise answers')",
                      zh: "(画像为空 — 试试说 \"记住我喜欢简洁的回答\")" },
  profDeleteKey:    { en: "remove this key", zh: "删除该字段" },

  // memory: skills
  skillEmpty:    { en: "(no skills loaded — drop *.md files into backend/data/skills/)",
                   zh: "(未加载技能 — 把 *.md 文件放到 backend/data/skills/)" },

  // trace panel
  traceTitle:   { en: "Live trace",   zh: "实时轨迹" },
  traceRunning: { en: "running",      zh: "运行中" },
  traceIdle:    { en: "idle",         zh: "空闲" },
  traceEmpty:   { en: "Send a message to see every node, tool call, sub-agent decision, and reflection in real time.",
                  zh: "发送一条消息，即可实时观察每一个节点、工具调用、子 agent 决策与反思过程。" },
};

/* Sample prompts shown in the empty chat state */
export const samples: { en: string; zh: string }[] = [
  {
    en: "Use the calculator to compute sqrt(2^32 - 1), then verify with Python.",
    zh: "用 calculator 算一下 (2^32 - 1) 的平方根，再用 Python 验证一遍",
  },
  {
    en: "Search the web for what's new in LangGraph 0.6 and summarise in 3 bullets.",
    zh: "搜一下 LangGraph 0.6 的新特性，用三条要点总结",
  },
  {
    en: "Remember that I prefer concise answers and cite sources when possible.",
    zh: "记住：我偏好简洁的回答，并尽量引用来源",
  },
  {
    en: "What did I tell you to remember earlier?",
    zh: "我之前让你记住了什么？",
  },
];

/* Architecture component list shown in the sidebar's Components tab */
export const components: { num: string; label: { en: string; zh: string }; note: { en: string; zh: string } }[] = [
  { num: "1", label: { en: "LLM ×2 tier",    zh: "双档模型" },
              note:  { en: "Sonnet for executor / sub-agents; Haiku for routers",
                       zh: "Sonnet 跑执行 / 子 agent；Haiku 跑路由 / 摘要 / 反思 / 抽取" } },
  { num: "2", label: { en: "Tools ×12",      zh: "12 个工具" },
              note:  { en: "calculator · web · files · grep · python · memory · profile · skills (top-K routed)",
                       zh: "计算器 · 联网 · 文件 · grep · Python · 记忆 · 画像 · 技能（按相关性 top-K 路由）" } },
  { num: "3", label: { en: "Memory ×4",      zh: "记忆 ×4" },
              note:  { en: "messages · vector facts · reflections · profile · skills",
                       zh: "消息窗口 · 向量事实 · 反思 · 画像 · 技能" } },
  { num: "4", label: { en: "Planning",       zh: "规划" },
              note:  { en: "ReAct + plan-and-execute (auto-routed)",
                       zh: "ReAct + 计划-执行（自动路由）" } },
  { num: "5", label: { en: "Perception",     zh: "感知" },
              note:  { en: "profile + facts (HyDE) + reflections + skills index, prompt-cache marked",
                       zh: "画像 + 事实（短查询走 HyDE）+ 反思 + 技能索引，标记 prompt cache" } },
  { num: "6", label: { en: "Action",         zh: "执行" },
              note:  { en: "Custom ToolNode: per-call timeout · LRU cache · result truncation",
                       zh: "自定义 ToolNode：单调用超时 · LRU 缓存 · 结果截断" } },
  { num: "S", label: { en: "Summarizer",     zh: "摘要器" },
              note:  { en: "auto-compact when messages > 24 (RemoveMessage + summary)",
                       zh: "消息 > 24 时自动压缩（RemoveMessage + 摘要）" } },
  { num: "X", label: { en: "Extractor",      zh: "记忆抽取" },
              note:  { en: "Mem0-style — async background write, gated, dedup'd before insert",
                       zh: "Mem0 风格 — 异步后台写入、启发式门控、去重后再写" } },
  { num: "+", label: { en: "Orchestrator",   zh: "编排器" },
              note:  { en: "supervisor → researcher / coder / writer",
                       zh: "supervisor 调度 researcher / coder / writer" } },
  { num: "+", label: { en: "Reflection",     zh: "反思" },
              note:  { en: "Reflexion-style self-critique (gated by heuristic; writes to reflections)",
                       zh: "Reflexion 式自我批评（启发式门控，跳过闲聊；命中即写反思库）" } },
  { num: "$", label: { en: "Cost guardrail", zh: "成本护栏" },
              note:  { en: "per-session USD budget enforced before each turn",
                       zh: "单会话美元预算，每轮前检查" } },
  { num: "+", label: { en: "Guardrails",     zh: "护栏" },
              note:  { en: "input prompt-injection · output PII",
                       zh: "输入：提示注入检测 · 输出：PII 脱敏" } },
  { num: "+", label: { en: "Observability",  zh: "可观测性" },
              note:  { en: "JSONL trace + live SSE event bus + token meter",
                       zh: "JSONL 落盘 + 实时 SSE 事件总线 + token 仪表" } },
];

/* ------------------------------------------------------------------ *
 * Provider                                                           *
 * ------------------------------------------------------------------ */
interface Ctx {
  lang: Lang;
  setLang: (l: Lang) => void;
  toggle: () => void;
  t: (key: keyof typeof ui) => string;
}

const LangContext = createContext<Ctx | null>(null);

function detectInitial(): Lang {
  if (typeof window === "undefined") return "zh";
  const saved = window.localStorage.getItem(STORAGE_KEY);
  if (saved === "en" || saved === "zh") return saved;
  // Default to zh if browser language starts with zh, otherwise en.
  return navigator.language?.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectInitial);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, lang);
    document.documentElement.lang = lang === "zh" ? "zh" : "en";
  }, [lang]);

  const setLang = useCallback((l: Lang) => setLangState(l), []);
  const toggle = useCallback(() => setLangState((l) => (l === "en" ? "zh" : "en")), []);
  const t = useCallback((key: keyof typeof ui) => ui[key]?.[lang] ?? key, [lang]);

  const value = useMemo(() => ({ lang, setLang, toggle, t }), [lang, setLang, toggle, t]);
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): Ctx {
  const ctx = useContext(LangContext);
  if (!ctx) throw new Error("useLang must be used inside <LangProvider>");
  return ctx;
}

/* Pick a localised string from a {en, zh} object */
export function pick<T extends { en: string; zh: string }>(obj: T, lang: Lang): string {
  return obj[lang];
}
