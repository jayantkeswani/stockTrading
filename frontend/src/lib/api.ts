const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch { /* no json body */ }
    throw new Error(detail);
  }
  return response.json();
}

export const api = {
  // Market
  getPrice: (symbol: string) => request(`/api/v1/market/price/${symbol}`),
  getAllPrices: () =>
    request<Record<string, { symbol: string; ltp: number; bid: number; ask: number; volume: number; change: number; change_pct: number; timestamp: string }>>(`/api/v1/market/prices`),
  refreshQuotes: () => request(`/api/v1/market/feed/refresh`, { method: "POST" }),
  getOHLCV: (symbol: string, { resolution, days }: { resolution?: string; days?: number } = {}) => {
    const params = new URLSearchParams();
    if (resolution) params.set("resolution", resolution);
    if (days) params.set("days", days.toString());
    return request<Array<{ timestamp: number; open: number; high: number; low: number; close: number; volume: number }>>(
      `/api/v1/market/ohlcv/${symbol}?${params}`
    );
  },
  getMarketStatus: () => request(`/api/v1/market/status`),
  searchSymbols: (query: string) =>
    request<{ results: Array<{ symbol: string; display: string; short_name: string; segment: string; strike: number; type: string; ltp: number; expiry: string; lot_size: number }> }>(
      `/api/v1/market/symbols/search?q=${encodeURIComponent(query)}`
    ),
  fetchBatchPrices: (symbols: string[]) =>
    request<Record<string, { symbol: string; ltp: number; bid: number; ask: number; volume: number; change: number; change_pct: number; timestamp: string }>>(
      `/api/v1/market/prices/batch`,
      { method: "POST", body: JSON.stringify({ symbols }) }
    ),
  startDataFeed: () => request(`/api/v1/market/feed/start`, { method: "POST" }),
  stopDataFeed: () => request(`/api/v1/market/feed/stop`, { method: "POST" }),
  getFyersStatus: () => request(`/api/v1/auth/fyers/status`),

  // Positions
  getPositions: () => request(`/api/v1/positions`),
  closePosition: (id: string, reason = "MANUAL") =>
    request(`/api/v1/positions/${id}/close`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  updateSL: (id: string, stopLoss: number) =>
    request(`/api/v1/positions/${id}/sl`, {
      method: "PATCH",
      body: JSON.stringify({ stop_loss: stopLoss }),
    }),

  // Trades
  getTrades: (params?: { status?: string; limit?: number }) => {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    if (params?.limit) query.set("limit", params.limit.toString());
    return request(`/api/v1/trades?${query}`);
  },
  getTradeSummary: () => request(`/api/v1/trades/summary`),
  closeTrade: (id: string, exitPrice?: number) =>
    request(`/api/v1/trades/${id}/close`, {
      method: "POST",
      body: JSON.stringify({ exit_price: exitPrice, reason: "MANUAL" }),
    }),

  // Signals
  getSignals: (params?: { status?: string }) => {
    const query = new URLSearchParams();
    if (params?.status) query.set("status", params.status);
    return request(`/api/v1/signals?${query}`);
  },
  getActiveSignals: () => request(`/api/v1/signals/active`),
  executeSignal: (id: string) =>
    request(`/api/v1/signals/${id}/execute`, { method: "POST" }),
  rejectSignal: (id: string) =>
    request(`/api/v1/signals/${id}/reject`, { method: "POST" }),

  // Risk
  getRiskDashboard: () => request(`/api/v1/risk/dashboard`),

  // Agent
  getAgentStatus: () => request(`/api/v1/agent/status`),
  startAgent: () => request(`/api/v1/agent/start`, { method: "POST" }),
  stopAgent: () => request(`/api/v1/agent/stop`, { method: "POST" }),
  getAgentLogs: (limit = 50) => request(`/api/v1/agent/logs?limit=${limit}`),
  confirmAction: (logId: string, approved: boolean) =>
    request(`/api/v1/agent/confirm/${logId}`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),
  toggleYolo: (enabled: boolean) =>
    request(`/api/v1/agent/yolo`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),

  // Watchlist
  getWatchlist: () =>
    request<{ items: Array<{ symbol: string; display: string; segment: string; strike: number | null; option_type: string | null; expiry: string }> }>(
      `/api/v1/watchlist`
    ),
  addToWatchlist: (item: { symbol: string; display: string; segment?: string; strike?: number | null; option_type?: string | null; expiry?: string }) =>
    request(`/api/v1/watchlist`, {
      method: "POST",
      body: JSON.stringify(item),
    }),
  removeFromWatchlist: (symbol: string) =>
    request(`/api/v1/watchlist/${encodeURIComponent(symbol)}`, {
      method: "DELETE",
    }),

  // Strategies
  getStrategies: () => request<Array<{
    id: string;
    strategy_name: string;
    is_active: boolean;
    auto_mode: boolean;
    parameters: Record<string, unknown>;
    risk_params: Record<string, unknown>;
    symbols: string[];
    timeframes: string[];
  }>>(`/api/v1/strategies`),
  toggleStrategy: (name: string) =>
    request<{ strategy: string; is_active: boolean }>(`/api/v1/strategies/${name}/toggle`, { method: "PATCH" }),
  toggleAutoMode: (name: string) =>
    request<{ strategy: string; auto_mode: boolean }>(`/api/v1/strategies/${name}/auto-mode`, { method: "PATCH" }),
  updateStrategy: (name: string, body: Record<string, unknown>) =>
    request(`/api/v1/strategies/${name}`, { method: "PUT", body: JSON.stringify(body) }),
  evaluateStrategy: (strategyName: string, symbol: string) =>
    request<{ symbol: string; strategy: string; signal_generated: boolean; signal_type: string | null; confidence: number | null }>(
      `/api/v1/strategies/evaluate`, { method: "POST", body: JSON.stringify({ strategy_name: strategyName, symbol }) }
    ),
  evaluateStrategyBatch: (strategyName: string) =>
    request<{ strategy: string; symbols_scanned: number; signals_generated: number; results: Array<{ symbol: string; signal_generated: boolean; signal_type: string | null; confidence: number | null }> }>(
      `/api/v1/strategies/evaluate/batch`, { method: "POST", body: JSON.stringify({ strategy_name: strategyName }) }
    ),

  // Tasks
  getTasks: () => request<{ tasks: import("./types").BackgroundTask[] }>(`/api/v1/tasks`),

  // Health
  health: () => request(`/api/v1/health`),
};
