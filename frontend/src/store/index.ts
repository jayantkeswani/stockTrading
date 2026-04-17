import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type {
  AgentLog,
  AgentStatus,
  MarketStatus,
  Position,
  PriceData,
  RiskDashboard,
  Signal,
} from "@/lib/types";

export interface ScanLogEntry {
  id: string;
  type: "start" | "end";
  strategy: string;
  timestamp: string;
  symbolsScanned?: number;
  signalsGenerated?: number;
}

interface AppState {
  // Prices
  prices: Record<string, PriceData>;
  updatePrice: (symbol: string, data: PriceData) => void;

  // Positions
  positions: Position[];
  setPositions: (positions: Position[]) => void;
  addPosition: (position: Position) => void;
  updatePosition: (id: string, updates: Partial<Position>) => void;
  removePosition: (id: string) => void;

  // Signals
  signals: Signal[];
  setSignals: (signals: Signal[]) => void;
  addSignal: (signal: Signal) => void;
  updateSignal: (id: string, updates: Partial<Signal>) => void;
  removeSignal: (id: string) => void;

  // Scan feed
  scanLogs: ScanLogEntry[];
  addScanLog: (entry: ScanLogEntry) => void;

  // Risk
  risk: RiskDashboard | null;
  setRisk: (risk: RiskDashboard) => void;

  // Market
  marketStatus: MarketStatus | null;
  setMarketStatus: (status: MarketStatus) => void;

  // Agent
  agentStatus: AgentStatus | null;
  setAgentStatus: (status: AgentStatus) => void;
  agentLogs: AgentLog[];
  setAgentLogs: (logs: AgentLog[]) => void;
  addAgentLog: (log: AgentLog) => void;

  // WebSocket
  wsConnected: boolean;
  setWsConnected: (connected: boolean) => void;

  // UI
  selectedSymbol: string;
  setSelectedSymbol: (symbol: string) => void;
}

export const useStore = create<AppState>()(
  persist(
    (set) => ({
      // Prices
      prices: {},
      updatePrice: (symbol, data) =>
        set((state) => ({
          prices: { ...state.prices, [symbol]: data },
        })),

      // Positions
      positions: [],
      setPositions: (positions) => set({ positions }),
      addPosition: (position) =>
        set((state) => ({
          positions: [position, ...state.positions.filter((p) => p.id !== position.id)],
        })),
      updatePosition: (id, updates) =>
        set((state) => ({
          positions: state.positions.map((p) =>
            p.id === id ? { ...p, ...updates } : p
          ),
        })),
      removePosition: (id) =>
        set((state) => ({
          positions: state.positions.filter((p) => p.id !== id),
        })),

      // Signals
      signals: [],
      setSignals: (signals) => set({ signals }),
      addSignal: (signal) =>
        set((state) => ({
          signals: [signal, ...state.signals.filter((s) => s.id !== signal.id)],
        })),
      updateSignal: (id, updates) =>
        set((state) => ({
          signals: state.signals.map((s) =>
            s.id === id ? { ...s, ...updates } : s
          ),
        })),
      removeSignal: (id) =>
        set((state) => ({
          signals: state.signals.filter((s) => s.id !== id),
        })),

      // Scan feed
      scanLogs: [],
      addScanLog: (entry) =>
        set((state) => ({ scanLogs: [entry, ...state.scanLogs].slice(0, 20) })),

      // Risk
      risk: null,
      setRisk: (risk) => set({ risk }),

      // Market
      marketStatus: null,
      setMarketStatus: (status) => set({ marketStatus: status }),

      // Agent
      agentStatus: null,
      setAgentStatus: (status) => set({ agentStatus: status }),
      agentLogs: [],
      setAgentLogs: (logs) => set({ agentLogs: logs }),
      addAgentLog: (log) =>
        set((state) => ({ agentLogs: [log, ...state.agentLogs] })),

      // WebSocket
      wsConnected: false,
      setWsConnected: (connected) => set({ wsConnected: connected }),

      // UI
      selectedSymbol: "NIFTY",
      setSelectedSymbol: (symbol) => set({ selectedSymbol: symbol }),
    }),
    {
      name: "scan-logs-storage",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ scanLogs: state.scanLogs }),
    }
  )
);
