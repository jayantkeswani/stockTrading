# Frontend - Next.js / React / TypeScript

## Tech Stack
- Next.js 15 (App Router)
- TypeScript (strict mode)
- Tailwind CSS v4 (dark theme only)
- Zustand for state management
- TradingView lightweight-charts for price charts
- WebSocket for real-time updates from backend on port 8080

## Module Map

### `src/app/` - Pages (App Router)
All pages use `'use client'` directive.
- `page.tsx` - Dashboard: 7-col left (ScannerPanel, ActivePositions, PnLCard, chart button) + 3-col right (Watchlist, AgentFeed). ChartModal overlay.
- `layout.tsx` - Root layout with AppShell wrapper
- `trades/page.tsx` - Trade history table with filters (status, symbol, date)
- `signals/page.tsx` - Signal history feed with status badges
- `settings/page.tsx` - Strategy configuration, risk parameter sliders
- `agent/page.tsx` - Agent dashboard: YOLO toggle, autonomy level badge, 5-card status grid, action logs
- `chart/page.tsx` - Full TradingView chart page with indicators overlay

### `src/components/` - React Components (by domain)

**layout/**
- `AppShell.tsx` - Main wrapper: sidebar + header + content area
- `Header.tsx` - Top nav: logo, IST clock, market status indicator, Fyers connection dot. Polls `getAllPrices()` + market status every 10s
- `Sidebar.tsx` - Left nav: page links, status indicators

**charts/**
- `PriceChart.tsx` - TradingView candlestick chart with VWAP/CPR overlays
- `ChartModal.tsx` - Modal wrapper for detailed chart view

**dashboard/**
- `PnLCard.tsx` - Daily/weekly/all-time P&L display
- `QuickStats.tsx` - Snapshot cards: capital, drawdown %, win %, ratio
- `Watchlist.tsx` - Symbol watchlist with debounced search (300ms), dropdown autocomplete from `/symbols/search` (local symbol master). Custom items stored in backend Redis via `/api/v1/watchlist` (enables agent to add symbols). Supports stocks, futures, options with segment badges (EQ/FUT/OPT).
- `SymbolSelector.tsx` - Index dropdown (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY)
- `ScannerPanel.tsx` - Real-time signal scanner
- `AgentFeed.tsx` - Agent action log (SL hits, profit confirmations, executions)

**positions/**
- `ActivePositions.tsx` - Table of open positions with unrealized P&L

**signals/**
- `SignalFeed.tsx` - Real-time signal stream with strategy, confidence, action buttons

### `src/hooks/` - Custom Hooks
- `useWebSocket.ts` - WebSocket connection to `ws://localhost:8080/ws`. Auto-reconnect. Subscribes to all 5 index symbols on connect. Exported `subscribeSymbols()` helper.

### `src/lib/` - Utilities
- `api.ts` - REST client: trades, signals, positions, agent, risk, market data. Key functions: `getAllPrices()`, `refreshQuotes()`, `searchSymbols()`, `toggleYolo()`
- `types.ts` - TypeScript interfaces for all entities (Trade, Signal, Position, AgentStatus with `yolo_mode` + `autonomy_level`)
- `formatters.ts` - INR currency (Indian number system: lakhs/crores), percentages, IST datetime
- `constants.ts` - `SYMBOLS` (5 indices), `STRATEGY_LABELS`, `STATUS_COLORS`, `WS_URL` (ws://localhost:8080/ws)

### `src/store/` - Zustand State
- `index.ts` - Single store with slices: prices (per symbol), positions, signals, risk metrics, agent status, market status. `updatePrice()` action used by Header polling + WebSocket events.

## Theme (Dark Only)
```
Background: #0a0a0f  (--bg-primary)
Surface:    #111118  (--bg-secondary)
Border:     #1e1e2e
Profit:     #00ff88
Loss:       #ff3366
Accent:     #6366f1  (indigo)
Warning:    #f59e0b  (amber, used for YOLO badge)
Font:       Geist Sans + Geist Mono
```

## Conventions
- All pages are client components (`'use client'`)
- All API calls go through `lib/api.ts` — never raw fetch in components
- WebSocket events go through `hooks/useWebSocket.ts`
- Currency formatted as INR with Indian number system (e.g., Rs 1,50,000)
- All 5 indices always referenced: NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY
- Backend API base URL: `http://localhost:8080/api/v1`
- WebSocket URL: `ws://localhost:8080/ws`

## How-To Guides

### Add a New Page
1. Create `src/app/{page-name}/page.tsx` with `'use client'`
2. Add nav link in `src/components/layout/Sidebar.tsx`
3. Create domain components in `src/components/{domain}/`
4. Add store slice in `src/store/index.ts` if page needs its own state

### Add a New Component
1. Place in `src/components/{domain}/` matching the page domain
2. Use Tailwind with theme variables (--bg-primary, --profit, --loss, etc.)
3. Get data from Zustand store or pass as props — don't fetch inside components
4. Use `formatINR()`, `formatPercent()` from `lib/formatters.ts`

### Add a New API Call
1. Add function in `src/lib/api.ts`
2. Add TypeScript types in `src/lib/types.ts`
3. Call from component or store action — not directly from hooks

### Add a New WebSocket Event
1. Add event type in `src/hooks/useWebSocket.ts` handler
2. Add store action in `src/store/index.ts` to process the event
3. Backend must publish to Redis channel for the event to flow through
