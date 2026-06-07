"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { STRATEGY_LABELS, STRATEGY_SETUPS, FILTERABLE_STRATEGIES } from "@/lib/constants";
import { useStore } from "@/store";
import type { YoloProfile } from "@/lib/types";

interface StrategyConfig {
  id: string;
  strategy_name: string;
  is_active: boolean;
  auto_mode: boolean;
  parameters: Record<string, unknown>;
  risk_params: Record<string, unknown>;
  symbols: string[];
  symbol_map?: Record<string, string>;
  timeframes: string[];
  shadow_enabled: boolean;
  yolo_enabled: boolean;
}

interface SymbolSuggestion {
  symbol: string;
  display: string;
  short_name: string;
  segment: string;
}

const SYMBOL_GROUPS: Record<string, string[]> = {
  "NIFTY 50 Stocks": [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
    "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK",
    "BAJFINANCE", "ASIANPAINT", "MARUTI", "TITAN", "SUNPHARMA",
    "TATAMOTORS", "ULTRACEMCO", "WIPRO", "NESTLEIND", "NTPC",
    "TATASTEEL", "POWERGRID", "M&M", "HCLTECH", "JSWSTEEL",
    "ADANIENT", "ADANIPORTS", "BAJAJFINSV", "TECHM", "ONGC",
    "COALINDIA", "HINDALCO", "GRASIM", "DIVISLAB", "DRREDDY",
    "CIPLA", "EICHERMOT", "HEROMOTOCO", "BPCL", "TATACONSUM",
    "APOLLOHOSP", "SBILIFE", "BRITANNIA", "LTIM", "INDUSINDBK",
    "BAJAJ-AUTO", "HDFCLIFE", "UPL",
  ],
  "Bank NIFTY Stocks": [
    "HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN",
    "INDUSINDBK", "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB",
    "PNB", "AUBANK", "BANKBARODA",
  ],
};

const INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"];

interface TradingSettings {
  capital: number;
  max_daily_drawdown_pct: number;
  max_risk_per_trade_pct: number;
  max_trades_per_day: number;
  paper_trading: boolean;
  autonomy_level: string;
  min_confidence_to_persist: number;
  min_confidence_for_shadow: number;
  min_confidence_for_execution: number;
  shadow_skip_permanent_watchlist: boolean;
  yolo_skip_permanent_watchlist: boolean;
  ai_overlay_enabled?: boolean;
}

export default function SettingsPage() {
  const [strategies, setStrategies] = useState<StrategyConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedStrategy, setExpandedStrategy] = useState<string | null>(null);

  const [tradingSettings, setTradingSettings] = useState<TradingSettings | null>(null);
  const [tradingDraft, setTradingDraft] = useState<Partial<TradingSettings>>({});
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const [yoloProfiles, setYoloProfiles] = useState<YoloProfile[]>([]);
  const [newProfileName, setNewProfileName] = useState("");
  const [newProfileCap, setNewProfileCap] = useState("");
  const committedProfilesRef = useRef<Map<string, { name: string; profit_cap: number }>>(new Map());

  useEffect(() => {
    async function load() {
      try {
        const [strats, cfg, profiles] = await Promise.all([
          api.getStrategies(),
          api.getTradingSettings(),
          api.getYoloProfiles() as Promise<YoloProfile[]>,
        ]);
        setStrategies(strats ?? []);
        setTradingSettings(cfg);
        setTradingDraft({});
        setYoloProfiles(profiles ?? []);
        const map = new Map<string, { name: string; profit_cap: number }>();
        for (const p of profiles ?? []) map.set(p.id, { name: p.name, profit_cap: p.profit_cap });
        committedProfilesRef.current = map;
      } catch {
        // API not ready
      }
      setLoading(false);
    }
    load();
  }, []);

  const currentSettings = tradingSettings ? { ...tradingSettings, ...tradingDraft } : null;

  const handleSaveTradingSettings = async () => {
    if (!tradingDraft || Object.keys(tradingDraft).length === 0) return;

    // Client-side validation: strip NaN/undefined and reject invalid values
    const clean: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(tradingDraft)) {
      if (v === undefined) continue;
      if (typeof v === "number" && isNaN(v)) continue;
      clean[k] = v;
    }
    if (Object.keys(clean).length === 0) return;

    // Cross-field confidence check (same invariant as backend)
    const cs = tradingSettings ? { ...tradingSettings, ...clean } : null;
    if (cs) {
      const p = cs.min_confidence_to_persist as number | undefined;
      const s = cs.min_confidence_for_shadow as number | undefined;
      const e = cs.min_confidence_for_execution as number | undefined;
      if (typeof p === "number" && typeof s === "number" && typeof e === "number" && !(p < s && s <= e)) {
        setSaveMsg(`Confidence must satisfy persist (${p}) < shadow (${s}) ≤ execution (${e})`);
        setTimeout(() => setSaveMsg(null), 5000);
        return;
      }
    }

    setSaving(true);
    setSaveMsg(null);
    try {
      await api.updateTradingSettings(clean);
      const latest = await api.getTradingSettings();
      setTradingSettings(latest as TradingSettings);
      setTradingDraft({});
      setSaveMsg("Saved");
      setTimeout(() => setSaveMsg(null), 2000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error saving";
      setSaveMsg(msg);
      setTimeout(() => setSaveMsg(null), 5000);
    } finally {
      setSaving(false);
    }
  };

  const handleToggleActive = async (name: string) => {
    try {
      const result = await api.toggleStrategy(name);
      setStrategies((prev) =>
        (Array.isArray(prev) ? prev : []).map((s) =>
          s.strategy_name === name ? { ...s, is_active: result.is_active } : s
        )
      );
    } catch {
      // Error
    }
  };

  const handleToggleAutoMode = async (name: string) => {
    try {
      const result = await api.toggleAutoMode(name);
      setStrategies((prev) =>
        (Array.isArray(prev) ? prev : []).map((s) =>
          s.strategy_name === name ? { ...s, auto_mode: result.auto_mode } : s
        )
      );
    } catch {
      // Error
    }
  };

  const handleToggleShadowEnabled = async (name: string, current: boolean) => {
    try {
      await api.updateStrategy(name, { shadow_enabled: !current });
      setStrategies((prev) =>
        (Array.isArray(prev) ? prev : []).map((s) =>
          s.strategy_name === name ? { ...s, shadow_enabled: !current } : s
        )
      );
    } catch {
      // Error
    }
  };

  const handleToggleYoloEnabled = async (name: string, current: boolean) => {
    try {
      await api.updateStrategy(name, { yolo_enabled: !current });
      setStrategies((prev) =>
        (Array.isArray(prev) ? prev : []).map((s) =>
          s.strategy_name === name ? { ...s, yolo_enabled: !current } : s
        )
      );
    } catch {
      // Error
    }
  };

  // Per-strategy AI overlay toggle — stored in the strategy's parameters JSONB
  // (ai_overlay_enabled, default true). Only effective when the master AI Overlay is on.
  const handleToggleStrategyAiOverlay = async (
    name: string, current: boolean, params: Record<string, unknown>,
  ) => {
    const newParams = { ...(params || {}), ai_overlay_enabled: !current };
    try {
      await api.updateStrategy(name, { parameters: newParams });
      setStrategies((prev) =>
        (Array.isArray(prev) ? prev : []).map((s) =>
          s.strategy_name === name ? { ...s, parameters: newParams } : s
        )
      );
    } catch {
      // Error
    }
  };

  // Optimistic local + store update for a YOLO profile, then PATCH.
  const patchProfileLocal = (id: string, patch: Partial<YoloProfile>) => {
    setYoloProfiles((prev) => {
      const updated = (Array.isArray(prev) ? prev : []).map((x) => (x.id === id ? { ...x, ...patch } : x));
      useStore.getState().setYoloProfiles(updated);
      return updated;
    });
  };

  const toggleProfileStrategy = async (p: YoloProfile, strat: string) => {
    const cur = p.strategies ?? [];
    const next = cur.includes(strat) ? cur.filter((s) => s !== strat) : [...cur, strat];
    // Prune setups that no longer belong to any selected strategy.
    const allowed = (next.length ? next : FILTERABLE_STRATEGIES).flatMap((s) => STRATEGY_SETUPS[s] ?? []);
    const nextSetups = (p.setups ?? []).filter((s) => allowed.includes(s));
    patchProfileLocal(p.id, { strategies: next, setups: nextSetups });
    try { await api.updateYoloProfile(p.id, { strategies: next, setups: nextSetups }); } catch { /* ignore */ }
  };

  const toggleProfileSetup = async (p: YoloProfile, setup: string) => {
    const cur = p.setups ?? [];
    const next = cur.includes(setup) ? cur.filter((s) => s !== setup) : [...cur, setup];
    patchProfileLocal(p.id, { setups: next });
    try { await api.updateYoloProfile(p.id, { setups: next }); } catch { /* ignore */ }
  };

  const handleUpdateParams = async (name: string, params: Record<string, unknown>) => {
    try {
      const updated = await api.updateStrategy(name, { parameters: params }) as StrategyConfig;
      setStrategies((prev) =>
        (Array.isArray(prev) ? prev : []).map((s) => (s.strategy_name === name ? { ...s, parameters: updated.parameters } : s))
      );
    } catch {
      // Error
    }
  };

  const handleUpdateSymbols = async (
    name: string,
    symbols: string[],
    symbolMap: Record<string, string>,
  ) => {
    try {
      await api.updateStrategy(name, { symbols, symbol_map: symbolMap });
      setStrategies((prev) =>
        (Array.isArray(prev) ? prev : []).map((s) =>
          s.strategy_name === name ? { ...s, symbols, symbol_map: { ...(s.symbol_map || {}), ...symbolMap } } : s
        )
      );
    } catch {
      // Error
    }
  };

  return (
    <div className="space-y-3">
      <h1 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
        Settings
      </h1>

      {/* Trading Config */}
      <div className="rounded border border-border bg-bg-secondary px-4 py-3">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
            Trading Parameters
          </h2>
          <div className="flex items-center gap-2">
            {saveMsg && (
              <span className={`text-[10px] font-mono max-w-xs truncate ${saveMsg === "Saved" ? "text-profit" : "text-loss"}`} title={saveMsg}>
                {saveMsg}
              </span>
            )}
            {Object.keys(tradingDraft).length > 0 && (
              <button
                onClick={handleSaveTradingSettings}
                disabled={saving}
                className="text-[10px] font-mono px-2 py-1 rounded bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-50"
              >
                {saving ? "saving…" : "Save"}
              </button>
            )}
          </div>
        </div>

        {currentSettings === null ? (
          <div className="text-xs font-mono text-text-muted">loading…</div>
        ) : (
          <>
            {/* Paper Trading Toggle */}
            <div className="flex items-center justify-between mb-3 pb-3 border-b border-border/40">
              <div>
                <span className="text-xs font-mono font-medium text-text-primary">Paper Trading</span>
                <p className="text-[10px] font-mono text-text-muted mt-0.5">
                  All trades simulated. No real money.
                </p>
              </div>
              <button
                onClick={() => setTradingDraft((d) => ({ ...d, paper_trading: !currentSettings.paper_trading }))}
                className={`w-8 h-4 rounded-full relative transition-colors ${currentSettings.paper_trading ? "bg-warning/40" : "bg-border"}`}
              >
                <div className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${currentSettings.paper_trading ? "left-4 bg-warning" : "left-0.5 bg-text-muted"}`} />
              </button>
            </div>

            {/* Permanent Watchlist Toggles */}
            <div className="flex items-center justify-between mb-3 pb-3 border-b border-border/40">
              <span className="text-xs font-mono font-medium text-text-primary">Skip Pinned Signals</span>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] font-mono text-text-muted">Shadow</span>
                  <button
                    onClick={() => setTradingDraft((d) => ({ ...d, shadow_skip_permanent_watchlist: !currentSettings.shadow_skip_permanent_watchlist }))}
                    className={`w-8 h-4 rounded-full relative transition-colors ${currentSettings.shadow_skip_permanent_watchlist ? "bg-accent/40" : "bg-border"}`}
                  >
                    <div className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${currentSettings.shadow_skip_permanent_watchlist ? "left-4 bg-accent" : "left-0.5 bg-text-muted"}`} />
                  </button>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] font-mono text-text-muted">YOLO</span>
                  <button
                    onClick={() => setTradingDraft((d) => ({ ...d, yolo_skip_permanent_watchlist: !currentSettings.yolo_skip_permanent_watchlist }))}
                    className={`w-8 h-4 rounded-full relative transition-colors ${currentSettings.yolo_skip_permanent_watchlist ? "bg-accent/40" : "bg-border"}`}
                  >
                    <div className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${currentSettings.yolo_skip_permanent_watchlist ? "left-4 bg-accent" : "left-0.5 bg-text-muted"}`} />
                  </button>
                </div>
              </div>
            </div>

            {/* AI Confidence Overlay — master switch (per-strategy toggle in the Strategies section) */}
            <div className="flex items-center justify-between mb-3 pb-3 border-b border-border/40">
              <div className="flex flex-col">
                <span className="text-xs font-mono font-medium text-text-primary">AI Confidence Overlay</span>
                <span className="text-[9px] font-mono text-text-muted">Master — off = no LLM overlay on any signal (removes the up-to-25s execution lag)</span>
              </div>
              <button
                onClick={() => setTradingDraft((d) => ({ ...d, ai_overlay_enabled: !(currentSettings.ai_overlay_enabled ?? true) }))}
                className={`w-8 h-4 rounded-full relative transition-colors ${(currentSettings.ai_overlay_enabled ?? true) ? "bg-accent/40" : "bg-border"}`}
              >
                <div className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${(currentSettings.ai_overlay_enabled ?? true) ? "left-4 bg-accent" : "left-0.5 bg-text-muted"}`} />
              </button>
            </div>

            {/* Risk grid */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">Capital (INR)</label>
                <input
                  type="number"
                  value={currentSettings.capital}
                  onChange={(e) => setTradingDraft((d) => ({ ...d, capital: Number(e.target.value) }))}
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">Max Daily Drawdown (%)</label>
                <input
                  type="number"
                  step="0.5"
                  value={currentSettings.max_daily_drawdown_pct}
                  onChange={(e) => setTradingDraft((d) => ({ ...d, max_daily_drawdown_pct: Number(e.target.value) }))}
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">Risk Per Trade (%)</label>
                <input
                  type="number"
                  step="0.5"
                  value={currentSettings.max_risk_per_trade_pct}
                  onChange={(e) => setTradingDraft((d) => ({ ...d, max_risk_per_trade_pct: Number(e.target.value) }))}
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">Max Trades / Day</label>
                <input
                  type="number"
                  value={currentSettings.max_trades_per_day}
                  onChange={(e) => setTradingDraft((d) => ({ ...d, max_trades_per_day: Number(e.target.value) }))}
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">Min Confidence to Persist</label>
                <input
                  type="number"
                  step="5"
                  min="0"
                  max="100"
                  value={currentSettings.min_confidence_to_persist ?? 0}
                  onChange={(e) => setTradingDraft((d) => ({ ...d, min_confidence_to_persist: Number(e.target.value) }))}
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">Min Confidence for Shadow</label>
                <input
                  type="number"
                  step="5"
                  min="0"
                  max="100"
                  value={currentSettings.min_confidence_for_shadow ?? 0}
                  onChange={(e) => setTradingDraft((d) => ({ ...d, min_confidence_for_shadow: Number(e.target.value) }))}
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">Min Confidence for Execution</label>
                <input
                  type="number"
                  step="5"
                  min="0"
                  max="100"
                  value={currentSettings.min_confidence_for_execution ?? 0}
                  onChange={(e) => setTradingDraft((d) => ({ ...d, min_confidence_for_execution: Number(e.target.value) }))}
                  className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
            </div>
          </>
        )}
      </div>

      {/* YOLO Profiles */}
      <div className="rounded border border-border bg-bg-secondary px-4 py-3">
        <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider mb-3">
          YOLO Profiles (Profit Caps)
        </h2>
        <div className="space-y-2">
          {(Array.isArray(yoloProfiles) ? [...yoloProfiles] : []).sort((a, b) => a.sort_order - b.sort_order).map((p, idx) => (
            <div key={p.id} className="flex items-center gap-3 flex-wrap px-2 py-1.5 rounded border border-border/50 bg-bg-tertiary/30">
              <input
                type="text"
                value={p.name}
                onChange={async (e) => {
                  const name = e.target.value;
                  setYoloProfiles((prev) => (Array.isArray(prev) ? prev : []).map((x) => x.id === p.id ? { ...x, name } : x));
                }}
                onBlur={async (e) => {
                  const committed = committedProfilesRef.current.get(p.id);
                  if (e.target.value !== committed?.name) {
                    try {
                      await api.updateYoloProfile(p.id, { name: e.target.value });
                      committedProfilesRef.current.set(p.id, { name: e.target.value, profit_cap: committed?.profit_cap ?? p.profit_cap });
                      setYoloProfiles((prev) => { useStore.getState().setYoloProfiles(prev); return prev; });
                    } catch { /* ignore */ }
                  }
                }}
                className="w-24 bg-bg-tertiary border border-border rounded px-2 py-1 text-xs font-mono focus:border-accent/50 focus:outline-none"
              />
              <div className="flex items-center gap-1">
                <span className="text-[9px] font-mono text-text-muted">Cap</span>
                <input
                  type="number"
                  step="1000"
                  min="0"
                  value={p.profit_cap}
                  onChange={(e) => {
                    const profit_cap = Number(e.target.value);
                    setYoloProfiles((prev) => (Array.isArray(prev) ? prev : []).map((x) => x.id === p.id ? { ...x, profit_cap } : x));
                  }}
                  onBlur={async (e) => {
                    const val = Number(e.target.value);
                    const committed = committedProfilesRef.current.get(p.id);
                    if (val !== committed?.profit_cap) {
                      try {
                        await api.updateYoloProfile(p.id, { profit_cap: val });
                        committedProfilesRef.current.set(p.id, { name: committed?.name ?? p.name, profit_cap: val });
                        setYoloProfiles((prev) => { useStore.getState().setYoloProfiles(prev); return prev; });
                      } catch { /* ignore */ }
                    }
                  }}
                  className="w-24 bg-bg-tertiary border border-border rounded px-2 py-1 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
              {/* Per-profile YOLO execution-confidence threshold (blank = inherit the global default) */}
              <div className="flex items-center gap-1">
                <span
                  className="text-[9px] font-mono text-text-muted"
                  title="Per-profile YOLO execution-confidence threshold. Blank = inherit the global default. A signal executes for this profile only when its confidence ≥ this value."
                >
                  MinConf
                </span>
                <input
                  type="number"
                  step="5"
                  min="0"
                  max="100"
                  placeholder="def"
                  value={p.min_confidence_for_execution ?? ""}
                  onChange={(e) => {
                    const raw = e.target.value;
                    const v = raw === "" ? null : Number(raw);
                    setYoloProfiles((prev) => (Array.isArray(prev) ? prev : []).map((x) => x.id === p.id ? { ...x, min_confidence_for_execution: v } : x));
                  }}
                  onBlur={async (e) => {
                    const raw = e.target.value;
                    const v = raw === "" ? null : Number(raw);
                    setYoloProfiles((prev) => {
                      const updated = (Array.isArray(prev) ? prev : []).map((x) => x.id === p.id ? { ...x, min_confidence_for_execution: v } : x);
                      useStore.getState().setYoloProfiles(updated);
                      return updated;
                    });
                    // Send -1 to CLEAR back to "inherit global" (the PATCH endpoint drops nulls).
                    try { await api.updateYoloProfile(p.id, { min_confidence_for_execution: v === null ? -1 : v }); } catch { /* ignore */ }
                  }}
                  title="Execution confidence (blank = inherit the global default)"
                  className="w-12 bg-bg-tertiary border border-border rounded px-1 py-1 text-xs font-mono focus:border-accent/50 focus:outline-none"
                />
              </div>
              {/* Thesis-invalidation exit (S5): close early when NIFTY bias flips STRONG-against the trade for N candles */}
              <div className="flex items-center gap-1">
                <span
                  className="text-[9px] font-mono text-text-muted"
                  title="S5 thesis-invalidation exit: close positions early when the NIFTY intraday bias flips STRONG-against the trade for N consecutive candles"
                >
                  Inval
                </span>
                <button
                  onClick={async () => {
                    const next = (p.invalidation_persist ?? 0) > 0 ? 0 : 3;
                    setYoloProfiles((prev) => {
                      const updated = (Array.isArray(prev) ? prev : []).map((x) => x.id === p.id ? { ...x, invalidation_persist: next } : x);
                      useStore.getState().setYoloProfiles(updated);
                      return updated;
                    });
                    try { await api.updateYoloProfile(p.id, { invalidation_persist: next }); } catch { /* ignore */ }
                  }}
                  title="Toggle thesis-invalidation exit"
                  className={`w-8 h-4 rounded-full relative transition-colors ${(p.invalidation_persist ?? 0) > 0 ? "bg-warning/40" : "bg-border"}`}
                >
                  <div className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${(p.invalidation_persist ?? 0) > 0 ? "left-4 bg-warning" : "left-0.5 bg-text-muted"}`} />
                </button>
                {(p.invalidation_persist ?? 0) > 0 && (
                  <>
                    <input
                      type="number"
                      min="1"
                      max="10"
                      step="1"
                      value={p.invalidation_persist ?? 3}
                      title="Consecutive opposing candles before exit (default 3)"
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        setYoloProfiles((prev) => (Array.isArray(prev) ? prev : []).map((x) => x.id === p.id ? { ...x, invalidation_persist: v } : x));
                      }}
                      onBlur={async (e) => {
                        const v = Math.max(1, Number(e.target.value) || 3);
                        setYoloProfiles((prev) => {
                          const updated = (Array.isArray(prev) ? prev : []).map((x) => x.id === p.id ? { ...x, invalidation_persist: v } : x);
                          useStore.getState().setYoloProfiles(updated);
                          return updated;
                        });
                        try { await api.updateYoloProfile(p.id, { invalidation_persist: v }); } catch { /* ignore */ }
                      }}
                      className="w-10 bg-bg-tertiary border border-border rounded px-1 py-1 text-xs font-mono focus:border-accent/50 focus:outline-none"
                    />
                    <button
                      onClick={async () => {
                        const next = !p.invalidation_quorum;
                        setYoloProfiles((prev) => {
                          const updated = (Array.isArray(prev) ? prev : []).map((x) => x.id === p.id ? { ...x, invalidation_quorum: next } : x);
                          useStore.getState().setYoloProfiles(updated);
                          return updated;
                        });
                        try { await api.updateYoloProfile(p.id, { invalidation_quorum: next }); } catch { /* ignore */ }
                      }}
                      title="Quorum: also require the stock to lose/reclaim its own VWAP (off by default)"
                      className={`text-[9px] font-mono px-1.5 py-0.5 rounded border transition-colors ${p.invalidation_quorum ? "border-warning/50 text-warning bg-warning/10" : "border-border text-text-muted hover:text-text-secondary"}`}
                    >
                      Q
                    </button>
                  </>
                )}
              </div>
              {idx > 0 ? (
                <button
                  onClick={async () => {
                    const next = !p.is_active;
                    setYoloProfiles((prev) => {
                      const updated = (Array.isArray(prev) ? prev : []).map((x) => x.id === p.id ? { ...x, is_active: next } : x);
                      useStore.getState().setYoloProfiles(updated);
                      return updated;
                    });
                    try { await api.updateYoloProfile(p.id, { is_active: next }); } catch { /* ignore */ }
                  }}
                  className={`w-8 h-4 rounded-full relative transition-colors ${p.is_active ? "bg-accent/40" : "bg-border"}`}
                >
                  <div className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${p.is_active ? "left-4 bg-accent" : "left-0.5 bg-text-muted"}`} />
                </button>
              ) : (
                <span className="text-[9px] font-mono text-text-muted w-8 text-center">DEFAULT</span>
              )}
              {idx > 0 && (
                <button
                  onClick={async () => {
                    if (!confirm(`Delete profile "${p.name}"?`)) return;
                    try {
                      await api.deleteYoloProfile(p.id);
                      setYoloProfiles((prev) => {
                        const updated = (Array.isArray(prev) ? prev : []).filter((x) => x.id !== p.id);
                        useStore.getState().setYoloProfiles(updated);
                        committedProfilesRef.current.delete(p.id);
                        return updated;
                      });
                    } catch { /* ignore */ }
                  }}
                  className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-loss/10 text-loss hover:bg-loss/20 transition-colors"
                >
                  DEL
                </button>
              )}
              {/* Strategy + setup execution filter — none selected = act on all signals */}
              <div className="basis-full flex items-center gap-1 flex-wrap pt-1.5 mt-1 border-t border-border/30">
                <span
                  className="text-[9px] font-mono text-text-muted"
                  title="Which strategies this profile executes. None selected = all."
                >
                  Strat
                </span>
                {FILTERABLE_STRATEGIES.map((strat) => {
                  const on = (p.strategies ?? []).includes(strat);
                  return (
                    <button
                      key={strat}
                      onClick={() => toggleProfileStrategy(p, strat)}
                      className={`text-[9px] font-mono px-1.5 py-0.5 rounded border transition-colors ${on ? "border-accent/50 text-accent bg-accent/10" : "border-border text-text-muted hover:text-text-secondary"}`}
                    >
                      {STRATEGY_LABELS[strat] ?? strat}
                    </button>
                  );
                })}
                {(() => {
                  const availableSetups = (p.strategies?.length ? p.strategies : FILTERABLE_STRATEGIES)
                    .flatMap((s) => STRATEGY_SETUPS[s] ?? []);
                  if (availableSetups.length === 0) return null;
                  return (
                    <>
                      <span
                        className="text-[9px] font-mono text-text-muted ml-1"
                        title="Which setups this profile executes. None selected = all."
                      >
                        Setup
                      </span>
                      {availableSetups.map((setup) => {
                        const on = (p.setups ?? []).includes(setup);
                        return (
                          <button
                            key={setup}
                            onClick={() => toggleProfileSetup(p, setup)}
                            className={`text-[9px] font-mono px-1.5 py-0.5 rounded border transition-colors ${on ? "border-accent/50 text-accent bg-accent/10" : "border-border text-text-muted hover:text-text-secondary"}`}
                          >
                            {setup}
                          </button>
                        );
                      })}
                    </>
                  );
                })()}
              </div>
            </div>
          ))}
          <div className="flex items-center gap-2 pt-1">
            <input
              type="text"
              placeholder="Name"
              value={newProfileName}
              onChange={(e) => setNewProfileName(e.target.value)}
              className="w-24 bg-bg-tertiary border border-border rounded px-2 py-1 text-xs font-mono focus:border-accent/50 focus:outline-none"
            />
            <input
              type="number"
              placeholder="Cap (INR)"
              step="1000"
              min="0"
              value={newProfileCap}
              onChange={(e) => setNewProfileCap(e.target.value)}
              className="w-24 bg-bg-tertiary border border-border rounded px-2 py-1 text-xs font-mono focus:border-accent/50 focus:outline-none"
            />
            <button
              disabled={!newProfileName.trim() || !newProfileCap}
              onClick={async () => {
                try {
                  const created = await api.createYoloProfile({ name: newProfileName.trim(), profit_cap: Number(newProfileCap) }) as YoloProfile;
                  setYoloProfiles((prev) => {
                    const arr = Array.isArray(prev) ? prev : [];
                    const updated = arr.some((x) => x.id === created.id) ? arr : [...arr, created];
                    useStore.getState().setYoloProfiles(updated);
                    committedProfilesRef.current.set(created.id, { name: created.name, profit_cap: created.profit_cap });
                    return updated;
                  });
                  setNewProfileName("");
                  setNewProfileCap("");
                } catch { /* ignore */ }
              }}
              className="text-[10px] font-mono px-2 py-1 rounded border border-accent/40 text-accent hover:bg-accent/10 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Add Profile
            </button>
          </div>
        </div>
      </div>

      {/* Strategy Config */}
      <div className="rounded border border-border bg-bg-secondary px-4 py-3">
        <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider mb-3">
          Strategies
        </h2>
        {loading ? (
          <div className="text-xs font-mono text-text-muted">loading...</div>
        ) : !Array.isArray(strategies) || strategies.length === 0 ? (
          <div className="text-xs font-mono text-text-muted">no strategies configured</div>
        ) : (
          <div className="space-y-1">
            {strategies.map((s) => {
              const label = STRATEGY_LABELS[s.strategy_name] || s.strategy_name;
              const isExpanded = expandedStrategy === s.strategy_name;

              return (
                <div key={s.strategy_name} className="border border-border/50 rounded overflow-hidden">
                  {/* Strategy header row */}
                  <div className="flex items-center justify-between px-3 py-1.5 bg-bg-tertiary/20">
                    <button
                      onClick={() => setExpandedStrategy(isExpanded ? null : s.strategy_name)}
                      className="flex items-center gap-1.5 text-left flex-1"
                    >
                      <svg
                        className={`w-3 h-3 text-text-muted transition-transform ${isExpanded ? "rotate-90" : ""}`}
                        fill="none" viewBox="0 0 24 24" stroke="currentColor"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                      <span className="text-xs font-mono font-medium">{label}</span>
                      {s.auto_mode && (
                        <span className="text-[10px] font-mono font-semibold px-1 py-px rounded bg-accent/15 text-accent">
                          AUTO
                        </span>
                      )}
                    </button>
                    <div className="flex items-center gap-2">
                      <div className="flex items-center gap-1">
                        <span className="text-[10px] font-mono text-text-muted">Shadow</span>
                        <ToggleSwitch
                          checked={s.shadow_enabled ?? true}
                          onChange={() => handleToggleShadowEnabled(s.strategy_name, s.shadow_enabled ?? true)}
                          color="accent"
                        />
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-[10px] font-mono text-text-muted">YOLO</span>
                        <ToggleSwitch
                          checked={s.yolo_enabled ?? true}
                          onChange={() => handleToggleYoloEnabled(s.strategy_name, s.yolo_enabled ?? true)}
                          color="warning"
                        />
                      </div>
                      <div className="flex items-center gap-1">
                        <span
                          className="text-[10px] font-mono text-text-muted"
                          title="AI confidence overlay for this strategy (needs the master AI Overlay on). Off = no LLM call → no execution lag."
                        >
                          AI
                        </span>
                        <ToggleSwitch
                          checked={((s.parameters?.ai_overlay_enabled as boolean | undefined) ?? true)}
                          onChange={() => handleToggleStrategyAiOverlay(
                            s.strategy_name,
                            (s.parameters?.ai_overlay_enabled as boolean | undefined) ?? true,
                            s.parameters,
                          )}
                          color="accent"
                        />
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-[10px] font-mono text-text-muted">Auto</span>
                        <ToggleSwitch
                          checked={s.auto_mode}
                          onChange={() => handleToggleAutoMode(s.strategy_name)}
                          color="accent"
                          disabled={!s.is_active}
                        />
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-[10px] font-mono text-text-muted">Active</span>
                        <ToggleSwitch
                          checked={s.is_active}
                          onChange={() => handleToggleActive(s.strategy_name)}
                          color="profit"
                        />
                      </div>
                    </div>
                  </div>

                  {/* Expanded: Symbol configuration + Parameters */}
                  {isExpanded && (
                    <div className="px-3 py-2 border-t border-border/30 animate-fade-in space-y-3">
                      <div>
                        <label className="text-[10px] font-mono text-text-muted uppercase block mb-1.5">
                          Symbols to scan
                        </label>
                        <SymbolSelector
                          selected={s.symbols}
                          symbolMap={s.symbol_map || {}}
                          onChange={(symbols, symbolMap) => handleUpdateSymbols(s.strategy_name, symbols, symbolMap)}
                        />
                      </div>
                      <StrategyParams
                        strategyName={s.strategy_name}
                        savedParams={s.parameters || {}}
                        onSave={(params) => handleUpdateParams(s.strategy_name, params)}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Notification Config */}
      <div className="rounded border border-border bg-bg-secondary px-4 py-3">
        <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider mb-3">
          Notifications
        </h2>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">
              Telegram Bot Token
            </label>
            <input
              type="password"
              placeholder="bot token..."
              className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none placeholder:text-text-muted"
            />
          </div>
          <div>
            <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">
              Telegram Chat ID
            </label>
            <input
              type="text"
              placeholder="chat id..."
              className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none placeholder:text-text-muted"
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Toggle Switch
// ------------------------------------------------------------------

function ToggleSwitch({
  checked,
  onChange,
  color = "profit",
  disabled = false,
}: {
  checked: boolean;
  onChange: () => void;
  color?: "profit" | "accent" | "warning";
  disabled?: boolean;
}) {
  const colorMap = {
    profit: { bg: "bg-profit/25", dot: "bg-profit" },
    accent: { bg: "bg-accent/25", dot: "bg-accent" },
    warning: { bg: "bg-warning/25", dot: "bg-warning" },
  };
  const c = colorMap[color];

  return (
    <button
      onClick={onChange}
      disabled={disabled}
      className={`w-7 h-3.5 rounded-full relative transition-colors ${
        disabled ? "opacity-30 cursor-not-allowed" : "cursor-pointer"
      } ${checked ? c.bg : "bg-bg-tertiary"}`}
    >
      <div
        className={`absolute top-0.5 w-2.5 h-2.5 rounded-full transition-all ${
          checked ? `right-0.5 ${c.dot}` : "left-0.5 bg-text-muted"
        }`}
      />
    </button>
  );
}

// ------------------------------------------------------------------
// Symbol Selector with autocomplete + groups
// ------------------------------------------------------------------

function SymbolSelector({
  selected,
  symbolMap,
  onChange,
}: {
  selected: string[];
  symbolMap: Record<string, string>;
  onChange: (symbols: string[], symbolMap: Record<string, string>) => void;
}) {
  const [input, setInput] = useState("");
  const [suggestions, setSuggestions] = useState<SymbolSuggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [showGroups, setShowGroups] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (input.trim().length < 1) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const result = await api.searchSymbols(input.trim());
        const filtered = result.results.filter(
          (r) => r.segment === "EQ" || r.segment === "FUT" || r.segment === "INDEX"
            || INDEX_SYMBOLS.includes(r.short_name)
        );
        setSuggestions(filtered);
        setShowSuggestions(filtered.length > 0);
      } catch {
        setSuggestions([]);
      }
    }, 300);

    return () => clearTimeout(debounceRef.current);
  }, [input]);

  const addSymbol = useCallback(
    (shortName: string, fyersSymbol?: string) => {
      if (!selected.includes(shortName)) {
        const newMap = { ...symbolMap };
        if (fyersSymbol) {
          newMap[shortName] = fyersSymbol;
        }
        onChange([...selected, shortName], newMap);
      }
      setInput("");
      setSuggestions([]);
      setShowSuggestions(false);
    },
    [selected, symbolMap, onChange]
  );

  const removeSymbol = useCallback(
    (sym: string) => {
      const newSymbols = selected.filter((s) => s !== sym);
      const newMap = { ...symbolMap };
      delete newMap[sym];
      onChange(newSymbols, newMap);
    },
    [selected, symbolMap, onChange]
  );

  const addGroup = useCallback(
    (groupName: string) => {
      const groupSymbols = SYMBOL_GROUPS[groupName] || [];
      const merged = [...new Set([...selected, ...groupSymbols])];
      onChange(merged, symbolMap);
      setShowGroups(false);
    },
    [selected, symbolMap, onChange]
  );

  return (
    <div className="space-y-1.5">
      {/* Selected symbols as chips */}
      <div className="flex flex-wrap gap-1">
        {selected.map((sym) => (
          <span
            key={sym}
            className="inline-flex items-center gap-0.5 px-1.5 py-px rounded bg-bg-tertiary border border-border text-xs font-mono text-text-secondary"
          >
            {sym}
            <button
              onClick={() => removeSymbol(sym)}
              className="text-text-muted hover:text-loss transition-colors"
            >
              <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </span>
        ))}
      </div>

      {/* Input + groups button */}
      <div className="flex gap-1.5 relative">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setShowSuggestions(false);
            if (e.key === "Enter" && input.trim()) {
              addSymbol(input.trim().toUpperCase());
            }
          }}
          placeholder="search stocks, indices, futures..."
          className="flex-1 text-xs font-mono bg-bg-tertiary border border-border rounded px-2 py-1 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50 transition-colors"
        />
        <div className="relative">
          <button
            onClick={() => setShowGroups(!showGroups)}
            className="text-xs font-mono px-2 py-1 rounded border border-border bg-bg-tertiary text-text-muted hover:text-text-secondary hover:border-accent/30 transition-colors"
          >
            +Group
          </button>
          {showGroups && (
            <div className="absolute right-0 top-full mt-1 bg-bg-secondary border border-border rounded shadow-lg z-50 min-w-[160px]">
              {Object.keys(SYMBOL_GROUPS).map((name) => (
                <button
                  key={name}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => addGroup(name)}
                  className="w-full text-left px-2 py-1 text-xs font-mono text-text-secondary hover:bg-bg-tertiary/80 hover:text-text-primary transition-colors"
                >
                  {name}
                  <span className="text-text-muted ml-1">({SYMBOL_GROUPS[name].length})</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Autocomplete dropdown */}
        {showSuggestions && suggestions.length > 0 && (
          <div className="absolute left-0 right-16 top-full mt-1 bg-bg-secondary border border-border rounded shadow-lg max-h-[180px] overflow-y-auto z-50">
            {suggestions.map((s) => (
              <button
                key={s.symbol}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => addSymbol(s.short_name || s.display, s.symbol)}
                className="w-full flex items-center gap-1.5 px-2 py-1 text-left hover:bg-bg-tertiary/80 transition-colors border-b border-border/20 last:border-0"
              >
                <span className="text-xs font-mono font-medium text-text-primary">{s.display}</span>
                <span className="text-[10px] font-mono text-text-muted">{s.segment}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Quick-add index buttons */}
      <div className="flex gap-1 flex-wrap">
        {INDEX_SYMBOLS.filter((s) => !selected.includes(s)).map((sym) => (
          <button
            key={sym}
            onClick={() => addSymbol(sym)}
            className="text-[10px] font-mono px-1 py-px rounded border border-border/50 text-text-muted hover:text-accent hover:border-accent/30 transition-colors"
          >
            +{sym}
          </button>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// Strategy Parameters Editor
// ------------------------------------------------------------------

const PARAM_LABELS: Record<string, string> = {
  vwap_proximity_pct: "VWAP Proximity %",
  vwap_min_distance_pct: "VWAP Min Distance %",
  sl_pct_aligned: "SL % (Bias Aligned)",
  sl_pct_unaligned: "SL % (Bias Unaligned)",
  default_target_multiplier: "Target Multiplier",
  vix_extreme: "VIX Extreme Threshold",
  sl_pct: "Stop Loss %",
  target_pct: "Target %",
  min_total_score: "Min CAN SLIM Score",
  max_vix: "Max VIX",
  breakout_volume_multiplier: "Volume Multiplier",
  max_positional_lots: "Max Lots",
  trailing_sl_activation_pct: "Trailing SL Activation %",
};

const HIDDEN_PARAMS = new Set(["trading_windows", "dead_zone", "min_confidence_to_persist", "min_confidence_for_shadow", "min_confidence_for_execution"]);

function StrategyParams({
  strategyName,
  savedParams,
  onSave,
}: {
  strategyName: string;
  savedParams: Record<string, unknown>;
  onSave: (params: Record<string, unknown>) => void;
}) {
  const [defaults, setDefaults] = useState<Record<string, unknown> | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [expanded, setExpanded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  useEffect(() => {
    api.getParameterDefaults(strategyName).then(setDefaults).catch(() => {});
  }, [strategyName]);

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
      onSave(toSave);
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
    <div>
      <div className="flex items-center justify-between">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 text-[10px] font-mono text-text-muted uppercase tracking-wider hover:text-text-secondary transition-colors"
        >
          <svg
            className={`w-2.5 h-2.5 transition-transform ${expanded ? "rotate-90" : ""}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          Parameters
        </button>
        <div className="flex items-center gap-2">
          {saveMsg && (
            <span className={`text-[10px] font-mono ${saveMsg === "Saved" ? "text-profit" : "text-loss"}`}>
              {saveMsg}
            </span>
          )}
          {hasDraft && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="text-[10px] font-mono px-2 py-0.5 rounded bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-50"
            >
              {saving ? "saving…" : "Save"}
            </button>
          )}
        </div>
      </div>

      {expanded && (
        <div className="mt-2 grid grid-cols-3 gap-2 animate-fade-in">
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
      )}
    </div>
  );
}
