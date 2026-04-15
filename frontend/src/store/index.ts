import { create } from "zustand";
import type {
  AgentLog,
  AgentStatus,
  MarketStatus,
  Position,
  PriceData,
  RiskDashboard,
  Signal,
} from "@/lib/types";

interface AppState {
  // Prices
  prices: Record<string, PriceData>;
  updatePrice: (symbol: string, data: PriceData) => void;

  // Positions
  positions: Position[];
  setPositions: (positions: Position[]) => void;
  updatePosition: (id: string, updates: Partial<Position>) => void;
  removePosition: (id: string) => void;

  // Signals
  signals: Signal[];
  setSignals: (signals: Signal[]) => void;
  addSignal: (signal: Signal) => void;

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

export const useStore = create<AppState>((set) => ({
  // Prices
  prices: {},
  updatePrice: (symbol, data) =>
    set((state) => ({
      prices: { ...state.prices, [symbol]: data },
    })),

  // Positions
  positions: [],
  setPositions: (positions) => set({ positions }),
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
    set((state) => ({ signals: [signal, ...state.signals] })),

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
}));
