/**
 * Curated demo prompts library.
 *
 * Each prompt is hand-picked to showcase one specific agent capability
 * (tool, memory tier, reasoning pattern, multi-agent mode, …).
 *
 * Used by `<ExamplePrompts />` (chat empty state) so first-time visitors can
 * try the agent without thinking up a query themselves.
 */
export type PromptCategory =
  | "tools"
  | "memory"
  | "reasoning"
  | "code"
  | "multi-agent"
  | "rag";

export interface DemoPrompt {
  /** Stable id so React keys never collide with localized text */
  id: string;
  /** Which capability this prompt demonstrates */
  category: PromptCategory;
  /** Bilingual prompt body (sent verbatim to the chat input) */
  text: { en: string; zh: string };
  /** Short hint shown under the title */
  hint: { en: string; zh: string };
  /** Engine recommendation — `null` = either works */
  prefer?: "graph" | "harness" | null;
}

export const CATEGORY_META: Record<
  PromptCategory,
  { label: { en: string; zh: string }; emoji: string; tint: string }
> = {
  tools: {
    label: { en: "Tools", zh: "工具" },
    emoji: "⚙",
    tint: "#7fffd4",
  },
  memory: {
    label: { en: "Memory", zh: "记忆" },
    emoji: "✿",
    tint: "#ffd166",
  },
  reasoning: {
    label: { en: "Reasoning", zh: "推理" },
    emoji: "✦",
    tint: "#cfc3ff",
  },
  code: {
    label: { en: "Code", zh: "代码" },
    emoji: "⌘",
    tint: "#a8e6cf",
  },
  "multi-agent": {
    label: { en: "Multi-Agent", zh: "多智能体" },
    emoji: "✺",
    tint: "#f4a261",
  },
  rag: {
    label: { en: "RAG", zh: "检索增强" },
    emoji: "❋",
    tint: "#ff8fab",
  },
};

export const DEMO_PROMPTS: DemoPrompt[] = [
  // ── Tools ───────────────────────────────────────────────────────────────
  {
    id: "tool-calc-verify",
    category: "tools",
    text: {
      en: "Use the calculator to compute sqrt(2^32 - 1), then verify with python_repl that the result squared equals 2^32 - 1.",
      zh: "用 calculator 算一下 (2^32 - 1) 的平方根，再用 python_repl 验证：把结果平方回去等于 2^32 - 1。",
    },
    hint: {
      en: "Cross-check between two tools",
      zh: "两个工具交叉校验",
    },
  },
  {
    id: "tool-time-zone",
    category: "tools",
    text: {
      en: "What time is it right now in Tokyo? Use current_time, then explain how many hours ahead/behind UTC it is.",
      zh: "现在东京几点？用 current_time，然后告诉我和 UTC 差几小时。",
    },
    hint: {
      en: "current_time tool, no extra context",
      zh: "current_time 工具，单步搞定",
    },
  },
  {
    id: "tool-file-grep",
    category: "tools",
    text: {
      en: "Read backend/app.py, then list every route prefix it registers, with the line numbers.",
      zh: "读一下 backend/app.py，列出它注册的所有 route 前缀和对应的行号。",
    },
    hint: {
      en: "read_file + structured output",
      zh: "read_file + 结构化输出",
    },
  },

  // ── Memory ──────────────────────────────────────────────────────────────
  {
    id: "mem-write",
    category: "memory",
    text: {
      en: "Remember that I prefer concise bullet-point answers and always want sources cited.",
      zh: "请记住：我喜欢简洁的要点式回答，回答时尽量引用来源。",
    },
    hint: {
      en: "Writes a profile preference",
      zh: "写入一条画像偏好",
    },
  },
  {
    id: "mem-recall",
    category: "memory",
    text: {
      en: "What did I tell you to remember earlier? List every preference you have on file.",
      zh: "我之前让你记住了什么？把已存的偏好都列出来。",
    },
    hint: {
      en: "Reads back from long-term memory",
      zh: "从长期记忆中召回",
    },
  },
  {
    id: "mem-skill",
    category: "memory",
    text: {
      en: "Use the 'refund-email-draft' skill to draft a refund request for a $1,200 ad campaign that did not deliver.",
      zh: "用 'refund-email-draft' 技能，帮我起草一封 1200 美元广告投放未达成的退款申请邮件。",
    },
    hint: {
      en: "Skill-as-context (Cursor / Claude pattern)",
      zh: "技能即上下文（Cursor / Claude 风格）",
    },
  },

  // ── Reasoning ───────────────────────────────────────────────────────────
  {
    id: "reasoning-plan",
    category: "reasoning",
    text: {
      en: "I have $5,000 to start a tiny SaaS. Plan the first 30 days week-by-week, with concrete deliverables and the cheapest possible stack.",
      zh: "我有 5000 美元想做一个 micro SaaS，请按周规划前 30 天的具体交付物，并给出最便宜的技术栈。",
    },
    hint: {
      en: "Multi-step plan-and-execute",
      zh: "多步规划-执行",
    },
  },
  {
    id: "reasoning-compare",
    category: "reasoning",
    text: {
      en: "Compare LangGraph and a plain while-loop harness for production agents. Give me 5 pros / 5 cons / when to pick which.",
      zh: "对比 LangGraph 和原生 while-loop harness 在生产 agent 中的差异：5 个优点 / 5 个缺点 / 各自适用场景。",
    },
    hint: {
      en: "Long-form structured comparison",
      zh: "长篇结构化对比",
    },
  },

  // ── Code ────────────────────────────────────────────────────────────────
  {
    id: "code-debug",
    category: "code",
    text: {
      en: "Here's a Python function that's supposed to merge two sorted lists but returns wrong results for empty input. Find the bug and fix it:\n```python\ndef merge(a, b):\n    out = []\n    i = j = 0\n    while i < len(a) and j < len(b):\n        if a[i] < b[j]:\n            out.append(a[i]); i += 1\n        else:\n            out.append(b[j]); j += 1\n    return out\n```",
      zh: "下面这个 Python 合并函数对空数组输入会返回错误结果，找出 bug 并修复：\n```python\ndef merge(a, b):\n    out = []\n    i = j = 0\n    while i < len(a) and j < len(b):\n        if a[i] < b[j]:\n            out.append(a[i]); i += 1\n        else:\n            out.append(b[j]); j += 1\n    return out\n```",
    },
    hint: {
      en: "Read code → reason → patch",
      zh: "读代码 → 推理 → 改 bug",
    },
    prefer: "harness",
  },
  {
    id: "code-write",
    category: "code",
    text: {
      en: "Write a Python script that watches a directory for new .csv files and prints their row counts. Use stdlib only. Show me the code, then explain it.",
      zh: "写一个 Python 脚本，监控某个目录下的新 .csv 文件，打印每个文件的行数。只用标准库。先给代码，再解释。",
    },
    hint: {
      en: "Generate runnable code with explanation",
      zh: "生成可运行代码 + 解释",
    },
  },

  // ── Multi-Agent ─────────────────────────────────────────────────────────
  {
    id: "ma-debate",
    category: "multi-agent",
    text: {
      en: "Run a 3-round debate between a 'pro-microservices' agent and a 'pro-monolith' agent on a Series-A SaaS startup. Then a judge agent picks the winner.",
      zh: "让一个「微服务派」和一个「单体派」就 A 轮 SaaS 创业公司的架构开 3 轮辩论，最后由一个裁判 agent 选出获胜方。",
    },
    hint: {
      en: "Triggers debate pattern · multi_agent_run",
      zh: "触发 debate 模式 · multi_agent_run",
    },
    prefer: "harness",
  },
  {
    id: "ma-research",
    category: "multi-agent",
    text: {
      en: "Use the supervisor → researcher → writer pipeline to produce a 1-page brief on 'is gVisor production-ready in 2026?'",
      zh: "用 supervisor → researcher → writer 流水线，写一份 1 页的简报：「gVisor 在 2026 年是否生产可用？」",
    },
    hint: {
      en: "Hierarchical sub-agents",
      zh: "层级化子 agent",
    },
    prefer: "graph",
  },

  // ── RAG ─────────────────────────────────────────────────────────────────
  {
    id: "rag-summarize",
    category: "rag",
    text: {
      en: "Search the web for what's new in LangGraph 0.6, then summarise the top 3 changes with a one-line impact assessment each.",
      zh: "联网搜 LangGraph 0.6 的新特性，挑出最重要的 3 项，每项配一句影响评估。",
    },
    hint: {
      en: "web_search → summarise → cite",
      zh: "联网搜 → 摘要 → 引用",
    },
  },
  {
    id: "rag-cite",
    category: "rag",
    text: {
      en: "Find 3 production case studies of teams running agents at >1M chats/day. For each, give: company, stack, key bottleneck, and a 1-link source.",
      zh: "搜 3 个真实案例：团队每天跑 100 万+ 次 agent 对话。每条给出：公司、技术栈、关键瓶颈、1 条来源链接。",
    },
    hint: {
      en: "Multi-source synthesis with citations",
      zh: "多源整合 + 引用",
    },
  },
];

/** Group prompts by category, preserving the array order */
export function groupByCategory(): Record<PromptCategory, DemoPrompt[]> {
  const out: Partial<Record<PromptCategory, DemoPrompt[]>> = {};
  for (const p of DEMO_PROMPTS) {
    (out[p.category] ||= []).push(p);
  }
  return out as Record<PromptCategory, DemoPrompt[]>;
}
