"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const PARAM_LABELS: Record<string, string> = {
  trailing_sl_breakeven_pct: "Trailing SL Breakeven %",
  trailing_sl_trail_pct: "Trailing SL Trail %",
  max_daily_drawdown_pct: "Max Daily Drawdown %",
  max_simultaneous_positions: "Max Positions",
  max_trades_per_day: "Max Trades/Day",
  rvol_threshold: "RVOL Threshold",
  rvol_caution_zone_threshold: "RVOL Caution Zone",
  min_adr: "Min ADR %",
};

const HIDDEN_PARAMS = new Set(["trading_windows", "dead_zone", "trailing_sl_enabled", "enabled_setups", "min_confidence_to_persist", "min_confidence_for_shadow", "min_confidence_for_execution"]);

const ALL_SETUPS = ["ORB", "VWAP_BOUNCE", "PDH_PDL", "GAP_CONTINUATION"] as const;
const SETUP_LABELS: Record<string, string> = {
  ORB: "ORB",
  VWAP_BOUNCE: "VWAP Bounce",
  PDH_PDL: "PDH/PDL",
  GAP_CONTINUATION: "Gap Cont.",
};

export function ConfigPanel() {
  const [defaults, setDefaults] = useState<Record<string, unknown> | null>(null);
  const [savedParams, setSavedParams] = useState<Record<string, unknown>>({});
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [expanded, setExpanded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  useEffect(() => {
    api.getParameterDefaults("intraday_futures").then(setDefaults).catch(() => {});
    api.getStrategies().then((strategies) => {
      const s5 = (strategies as Array<{ strategy_name: string; parameters: Record<string, unknown> }>)
        .find((s) => s.strategy_name === "intraday_futures");
      if (s5?.parameters) setSavedParams(s5.parameters);
    }).catch(() => {});
  }, []);

  if (!defaults) return null;

  const merged = { ...defaults, ...savedParams, ...draft };
  const hasDraft = Object.keys(draft).length > 0;

  const numericKeys = Object.keys(defaults).filter(
    (k) => typeof defaults[k] === "number" && !HIDDEN_PARAMS.has(k)
  );

  const handleSave = async () => {
    setSaving(true);
    setSaveMsg(null);
    const toSave = { ...savedParams, ...draft };
    try {
      await api.updateStrategy("intraday_futures", { parameters: toSave });
      setSavedParams(toSave);
      setDraft({});
      setSaveMsg("Saved");
      setTimeout(() => setSaveMsg(null), 2000);
    } catch {
      setSaveMsg("Error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border border-border rounded bg-bg-secondary">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-1.5 border-b border-border hover:bg-bg-tertiary"
      >
        <span className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Config
        </span>
        <div className="flex items-center gap-2">
          {saveMsg && (
            <span className={`text-[10px] font-mono ${saveMsg === "Saved" ? "text-profit" : "text-loss"}`}>
              {saveMsg}
            </span>
          )}
          {hasDraft && (
            <button
              onClick={(e) => { e.stopPropagation(); handleSave(); }}
              disabled={saving}
              className="text-[10px] font-mono px-2 py-0.5 rounded bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-50"
            >
              {saving ? "saving..." : "Save"}
            </button>
          )}
          <span className="text-text-muted text-xs">{expanded ? "▾" : "▸"}</span>
        </div>
      </button>

      {expanded && (
        <div className="px-3 py-2 animate-fade-in space-y-2">
          <div className="grid grid-cols-2 gap-2">
            {numericKeys.map((key) => (
              <div key={key}>
                <label className="text-[10px] font-mono text-text-muted block mb-0.5 truncate" title={key}>
                  {PARAM_LABELS[key] || key}
                </label>
                <input
                  type="number"
                  step="any"
                  value={merged[key] as number}
                  onChange={(e) => setDraft((d) => ({ ...d, [key]: Number(e.target.value) }))}
                  className="w-full bg-bg-tertiary border border-border rounded px-1.5 py-1 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
            ))}
          </div>
          <div>
            <label className="text-[10px] font-mono text-text-muted block mb-1 uppercase tracking-wider">
              Enabled Setups
            </label>
            <div className="flex flex-wrap gap-2">
              {ALL_SETUPS.map((setup) => {
                const enabled = ((merged.enabled_setups as string[]) ?? []).includes(setup);
                return (
                  <label key={setup} className="flex items-center gap-1 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={enabled}
                      onChange={() => {
                        const current = ((merged.enabled_setups as string[]) ?? []);
                        const next = enabled
                          ? current.filter((s) => s !== setup)
                          : [...current, setup];
                        setDraft((d) => ({ ...d, enabled_setups: next }));
                      }}
                      className="accent-accent w-3 h-3"
                    />
                    <span className="text-[10px] font-mono text-text-secondary">
                      {SETUP_LABELS[setup]}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
