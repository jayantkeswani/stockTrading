import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type {
  AgentLog,
  AgentStatus,
  MarketStatus,
  Position,
  PriceData,
  ResearchAgentStatus,
  ResearchReportListItem,
  RiskDashboard,
  Signal,
  Trade,
} from "@/lib/types";
import type { Timeframe } from "@/lib/constants";

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

  // Closed today
  closedToday: Trade[];
  setClosedToday: (trades: Trade[]) => void;
  prependClosedTrade: (trade: Trade) => void;

  // Position view mode (Real vs Shadow/Signal Test)
  positionViewMode: "REAL" | "SHADOW";
  setPositionViewMode: (mode: "REAL" | "SHADOW") => void;
  shadowPositions: Position[];
  setShadowPositions: (positions: Position[]) => void;
  shadowClosedToday: Trade[];
  setShadowClosedToday: (trades: Trade[]) => void;

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

  // Research
  activeResearches: Record<string, {
    reportId: string;
    symbol: string;
    status: "in_progress" | "completed" | "failed";
    agentStatuses: ResearchAgentStatus[];
  }>;
  selectedResearchId: string | null;
  researchReports: ResearchReportListItem[];
  startResearchSession: (reportId: string, symbol: string, agentsTotal: number) => void;
  updateResearchAgent: (reportId: string, agentName: string, update: Partial<ResearchAgentStatus>) => void;
  completeResearch: (reportId: string, status: "completed" | "failed") => void;
  setSelectedResearchId: (id: string | null) => void;
  setResearchReports: (reports: ResearchReportListItem[]) => void;

  // Watchlist
  watchlistItems: Array<{ symbol: string; display: string; segment: string }>;
  setWatchlistItems: (items: Array<{ symbol: string; display: string; segment: string }>) => void;
  addWatchlistItem: (item: { symbol: string; display: string; segment: string }) => void;
  removeWatchlistItem: (symbol: string) => void;

  // UI
  selectedSymbol: string;
  setSelectedSymbol: (symbol: string) => void;
  activeTimeframe: Timeframe;
  setActiveTimeframe: (tf: Timeframe) => void;
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

      // Closed today
      closedToday: [],
      setClosedToday: (trades) => set({ closedToday: trades }),
      prependClosedTrade: (trade) =>
        set((state) => ({
          closedToday: [trade, ...state.closedToday.filter((t) => t.id !== trade.id)],
        })),

      // Position view mode
      positionViewMode: "REAL",
      setPositionViewMode: (mode) => set({ positionViewMode: mode }),
      shadowPositions: [],
      setShadowPositions: (shadowPositions) => set({ shadowPositions }),
      shadowClosedToday: [],
      setShadowClosedToday: (shadowClosedToday) => set({ shadowClosedToday }),

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

      // Research
      activeResearches: {},
      selectedResearchId: null,
      researchReports: [],
      startResearchSession: (reportId, symbol, agentsTotal) =>
        set((state) => {
          const agents: ResearchAgentStatus[] = [
            { name: "fundamental", description: "Analyzing earnings and growth", status: "pending" },
            { name: "technical", description: "Analyzing price trends and patterns", status: "pending" },
            { name: "oi_derivatives", description: "Analyzing open interest data", status: "pending" },
            { name: "institutional", description: "Analyzing FII/DII/MF holdings", status: "pending" },
            { name: "news_sentiment", description: "Searching for news and sentiment", status: "pending" },
            { name: "valuation", description: "Analyzing valuation metrics", status: "pending" },
          ];
          return {
            activeResearches: {
              ...state.activeResearches,
              [reportId]: { reportId, symbol, status: "in_progress", agentStatuses: agents },
            },
            selectedResearchId: reportId,
          };
        }),
      updateResearchAgent: (reportId, agentName, update) =>
        set((state) => {
          const research = state.activeResearches[reportId];
          if (!research) return state;
          return {
            activeResearches: {
              ...state.activeResearches,
              [reportId]: {
                ...research,
                agentStatuses: research.agentStatuses.map((a) =>
                  a.name === agentName ? { ...a, ...update } : a
                ),
              },
            },
          };
        }),
      completeResearch: (reportId, status) =>
        set((state) => {
          const research = state.activeResearches[reportId];
          if (!research) return state;
          return {
            activeResearches: {
              ...state.activeResearches,
              [reportId]: { ...research, status },
            },
          };
        }),
      setSelectedResearchId: (id) => set({ selectedResearchId: id }),
      setResearchReports: (reports) => set({ researchReports: reports }),

      // UI
      watchlistItems: [],
      setWatchlistItems: (items) => set({ watchlistItems: items }),
      addWatchlistItem: (item) =>
        set((state) => ({
          watchlistItems: state.watchlistItems.some((w) => w.symbol === item.symbol)
            ? state.watchlistItems
            : [...state.watchlistItems, item],
        })),
      removeWatchlistItem: (symbol) =>
        set((state) => ({
          watchlistItems: state.watchlistItems.filter((w) => w.symbol !== symbol),
        })),

      selectedSymbol: "NIFTY",
      setSelectedSymbol: (symbol) => set({ selectedSymbol: symbol }),
      activeTimeframe: "5m",
      setActiveTimeframe: (tf) => set({ activeTimeframe: tf }),
    }),
    {
      name: "scan-logs-storage",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ scanLogs: state.scanLogs, activeTimeframe: state.activeTimeframe }),
    }
  )
);
