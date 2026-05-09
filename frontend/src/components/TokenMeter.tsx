import { useEffect, useState } from "react";
import { fetchUsage, type UsageBucket, type UsageSnapshot } from "../api";
import { useLang } from "../i18n";

interface Props {
  sessionId: string | null;
  /** When the agent finishes a turn, parent bumps this so we re-poll instantly. */
  refreshTick: number;
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(2) + "k";
  if (n < 1_000_000) return (n / 1000).toFixed(1) + "k";
  return (n / 1_000_000).toFixed(2) + "M";
}

function fmtUSD(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.001) return "<$0.001";
  if (usd < 1) return "$" + usd.toFixed(4);
  return "$" + usd.toFixed(2);
}

interface BucketProps {
  label: string;
  b: UsageBucket | undefined;
  tips: { up: string; dn: string; cache: string; cost: string; calls: string };
}

function Bucket({ label, b, tips }: BucketProps) {
  if (!b)
    return (
      <div className="meter-bucket">
        <div className="meter-label">{label}</div>
        <div className="meter-line muted">—</div>
      </div>
    );
  return (
    <div className="meter-bucket">
      <div className="meter-label">{label}</div>
      <div className="meter-line">
        <span className="up" title={tips.up}>↑ {fmtTokens(b.input)}</span>
        <span className="dn" title={tips.dn}>↓ {fmtTokens(b.output)}</span>
        {b.cache_read > 0 && (
          <span className="cache" title={tips.cache}>cache {fmtTokens(b.cache_read)}</span>
        )}
        <span className="cost" title={tips.cost}>{fmtUSD(b.cost_usd)}</span>
        <span className="calls" title={tips.calls}>{b.calls}×</span>
      </div>
    </div>
  );
}

export default function TokenMeter({ sessionId, refreshTick }: Props) {
  const { t } = useLang();
  const [snap, setSnap] = useState<UsageSnapshot | null>(null);

  const refresh = () => {
    fetchUsage(sessionId).then(setSnap).catch(() => {});
  };

  useEffect(() => {
    refresh();
    // gentle background poll so the global counter ticks even when this UI tab is idle
    const id = window.setInterval(refresh, 5000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // bump on each turn-complete
  useEffect(() => {
    if (refreshTick > 0) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTick]);

  const tips = {
    up:    t("meterTipUp"),
    dn:    t("meterTipDn"),
    cache: t("meterTipCache"),
    cost:  t("meterTipCost"),
    calls: t("meterTipCalls"),
  };

  const sessSpent = snap?.session?.cost_usd ?? 0;
  const budget = snap?.budget_usd ?? 0;
  const pct = budget > 0 ? Math.min(100, Math.round((sessSpent / budget) * 100)) : 0;
  const overBudget = !!snap?.over_budget;

  return (
    <div
      className={`meter ${overBudget ? "over-budget" : ""}`}
      title={snap?.pricing_known ? "" : t("meterTipNoPricing")}
    >
      <Bucket label={t("meterThisTurn")} b={snap?.session} tips={tips} />
      <span className="meter-sep" />
      <Bucket label={t("meterTotal")} b={snap?.global} tips={tips} />
      {budget > 0 && (
        <>
          <span className="meter-sep" />
          <div className="meter-bucket meter-budget" title={t("meterTipBudget")}>
            <div className="meter-label">
              {t("meterBudget")} ${budget.toFixed(2)}
            </div>
            <div className="meter-budget-bar">
              <div
                className="meter-budget-fill"
                style={{ width: `${pct}%` }}
                data-state={overBudget ? "over" : pct > 80 ? "warn" : "ok"}
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
