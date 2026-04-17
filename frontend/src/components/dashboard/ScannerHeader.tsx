"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { STRATEGY_LABELS } from "@/lib/constants";
import { useStore } from "@/store";

interface StrategyConfig {
  strategy_name: string;
  is_active: boolean;
  auto_mode: boolean;
  symbols: string[];
}

export function ScannerHeader() {
  const [strategies, setStrategies] = useState<StrategyConfig[]>([]);
  const [scanning, setScanning] = useState<string | null>(null);
  const { addScanLog } = useStore();

  useEffect(() => {
    async function load() {
      try {
        const data = await api.getStrategies();
        setStrategies(data.filter((s) => s.is_active));
      } catch {
        // API not ready
      }
    }
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleScan = async (strategyName: string) => {
    if (scanning) return;
    setScanning(strategyName);

    const scanId = Date.now().toString();
    const label = STRATEGY_LABELS[strategyName] || strategyName;

    addScanLog({
      id: `${scanId}-start`,
      type: "start",
      strategy: label,
      timestamp: new Date().toISOString(),
    });

    try {
      const result = await api.evaluateStrategyBatch(strategyName);
      addScanLog({
        id: `${scanId}-end`,
        type: "end",
        strategy: label,
        timestamp: new Date().toISOString(),
        symbolsScanned: result.symbols_scanned,
        signalsGenerated: result.signals_generated,
      });
    } catch (err) {
      addScanLog({
        id: `${scanId}-end`,
        type: "end",
        strategy: label,
        timestamp: new Date().toISOString(),
        symbolsScanned: 0,
        signalsGenerated: 0,
      });
    } finally {
      setScanning(null);
    }
  };

  if (strategies.length === 0) return null;

  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded border border-border bg-bg-secondary">
      {strategies.map((s) => {
        const label = STRATEGY_LABELS[s.strategy_name] || s.strategy_name;
        const isScanning = scanning === s.strategy_name;
        return (
          <button
            key={s.strategy_name}
            onClick={() => handleScan(s.strategy_name)}
            disabled={!!scanning}
            className={`
              inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono font-medium
              transition-all duration-150
              ${isScanning
                ? "bg-accent/20 text-accent border border-accent/30"
                : "text-text-muted border border-transparent hover:text-text-secondary hover:bg-bg-tertiary"
              }
              disabled:opacity-40 disabled:cursor-not-allowed
            `}
          >
            {isScanning && (
              <svg className="w-2.5 h-2.5 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            {label}
          </button>
        );
      })}
      {scanning && (
        <span className="text-xs text-accent ml-auto font-mono animate-pulse">
          scanning...
        </span>
      )}
    </div>
  );
}
