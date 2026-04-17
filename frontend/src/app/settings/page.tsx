"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { STRATEGY_LABELS } from "@/lib/constants";

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

export default function SettingsPage() {
  const [strategies, setStrategies] = useState<StrategyConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedStrategy, setExpandedStrategy] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const data = await api.getStrategies();
        setStrategies(data);
      } catch {
        // API not ready
      }
      setLoading(false);
    }
    load();
  }, []);

  const handleToggleActive = async (name: string) => {
    try {
      const result = await api.toggleStrategy(name);
      setStrategies((prev) =>
        prev.map((s) =>
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
        prev.map((s) =>
          s.strategy_name === name ? { ...s, auto_mode: result.auto_mode } : s
        )
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
        prev.map((s) =>
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

      {/* Paper Trading Toggle */}
      <div className="rounded border border-border bg-bg-secondary px-4 py-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xs font-mono font-medium text-text-primary">Paper Trading Mode</h2>
            <p className="text-xs font-mono text-text-muted mt-0.5">
              All trades simulated. No real money.
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-mono text-warning font-medium">PAPER</span>
            <div className="w-8 h-4 bg-warning/25 rounded-full relative">
              <div className="absolute left-0.5 top-0.5 w-3 h-3 bg-warning rounded-full" />
            </div>
          </div>
        </div>
      </div>

      {/* Risk Parameters */}
      <div className="rounded border border-border bg-bg-secondary px-4 py-3">
        <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider mb-3">
          Risk Management
        </h2>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">
              Capital (INR)
            </label>
            <input
              type="text"
              defaultValue="10,00,000"
              className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
            />
          </div>
          <div>
            <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">
              Max Daily Drawdown (%)
            </label>
            <input
              type="number"
              defaultValue={5}
              className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
            />
          </div>
          <div>
            <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">
              Risk Per Trade (%)
            </label>
            <input
              type="number"
              defaultValue={2}
              className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
            />
          </div>
          <div>
            <label className="text-[10px] font-mono text-text-muted uppercase block mb-1">
              Max Trades / Day
            </label>
            <input
              type="number"
              defaultValue={3}
              className="w-full bg-bg-tertiary border border-border rounded px-2 py-1.5 text-xs font-mono focus:border-accent/50 focus:outline-none"
            />
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
        ) : strategies.length === 0 ? (
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

                  {/* Expanded: Symbol configuration */}
                  {isExpanded && (
                    <div className="px-3 py-2 border-t border-border/30 animate-fade-in">
                      <label className="text-[10px] font-mono text-text-muted uppercase block mb-1.5">
                        Symbols to scan
                      </label>
                      <SymbolSelector
                        selected={s.symbols}
                        symbolMap={s.symbol_map || {}}
                        onChange={(symbols, symbolMap) => handleUpdateSymbols(s.strategy_name, symbols, symbolMap)}
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
    if (input.trim().length < 2) {
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
