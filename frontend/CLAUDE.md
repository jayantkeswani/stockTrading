# Frontend - Next.js / React / TypeScript

## Tech Stack
- Next.js 15 (App Router)
- TypeScript (strict mode)
- Tailwind CSS v4 (dark theme only — institutional terminal aesthetic)
- Zustand for state management
- TradingView lightweight-charts for price charts
- WebSocket for real-time updates from backend on port 8080

## Module Map

### `src/app/` - Pages (App Router)
All pages use `'use client'` directive.
- `page.tsx` - Dashboard: full-width PnL strip at top, then 8-col left (ScannerHeader, ScannerPanel, ActivePositions) + 4-col right (Watchlist, ScanFeed, AgentFeed). Loads pending signals from DB on mount. ChartModal overlay.
- `layout.tsx` - Root layout with AppShell wrapper
- `trades/page.tsx` - Trade history table with compact monospace rows
- `signals/page.tsx` - Signal history feed with status badges
- `settings/page.tsx` - Strategy configuration (is_active, auto_mode, symbols per strategy with autocomplete + group presets), risk parameters. Wired to backend `/api/v1/strategies` endpoints. When a symbol is added via autocomplete, the full Fyers symbol is sent alongside (`symbol_map`) so the backend never needs to reconstruct it. Group-add symbols are auto-resolved server-side via the symbol master.
- `agent/page.tsx` - Agent dashboard: YOLO toggle, autonomy level badge, 5-card status grid, action logs
- `chart/page.tsx` - Full TradingView chart page with symbol tabs

### `src/components/` - React Components (by domain)

**layout/**
- `AppShell.tsx` - Main wrapper: sidebar (48px) + header (36px) + content area. Applies `noise-bg` texture class.
- `Header.tsx` - Thin status bar (36px): IST clock, market status, VIX, Tasks/Fyers/Agent/WS indicators with monospace labels. Polls `getAllPrices()` + market status every 10s.
- `TasksPopup.tsx` - Background tasks popup (click-to-open). Shows all registered tasks with status dots, type badges (scheduler/startup/service), schedule/description metadata, timestamps, errors. Polls `GET /api/v1/tasks` every 5s while open. Click-outside to close.
- `AgentPopup.tsx` - Agent control popup (click-to-open). Start/stop agent, toggle SEMI/YOLO mode. Shows positions monitored, pending confirmations, uptime. Reads from Zustand store, writes via API. Click-outside to close.
- `Sidebar.tsx` - Narrow icon rail (48px): page links with hover tooltips, paper trading indicator

**charts/**
- `PriceChart.tsx` - TradingView candlestick chart with real data from Fyers (via backend proxy `GET /ohlcv`). Supports 1m/5m/15m/1h/1D timeframes. Loading spinner during fetch.
- `ChartModal.tsx` - Modal wrapper for detailed chart view

**dashboard/**
- `PnLCard.tsx` - Single-line horizontal strip: P&L, drawdown (with thin bar), trades count, capital at risk — all inline with dividers. Halted badge inline.
- `Watchlist.tsx` - Uniform symbol list (no visual distinction between default indices and custom items). Debounced search (300ms), dropdown autocomplete from `/symbols/search` (local symbol master). Custom items stored in backend Redis via `/api/v1/watchlist`. Supports stocks, futures, options with segment badges (FUT/OPT). Max-height 300px with scroll.
- `ScannerHeader.tsx` - Ultra-compact strategy pill bar. Monospace text-only buttons (no icons). Triggers manual batch evaluation via `POST /api/v1/strategies/evaluate/batch`. Logs start/end entries to ScanFeed.
- `ScannerPanel.tsx` - Dense signal table. Signals rendered as compact rows (not cards) with inline direction arrow, symbol, price levels, R:R ratio, confidence, strategy badge, and EXEC/dismiss buttons. Click row to expand reason text. Max-height 340px.
- `ScanFeed.tsx` - Compact scan log. Shows scan start/end messages with stats. Max-height 160px.
- `AgentFeed.tsx` - Agent action log grouped by strategy. Compact filter dropdowns. Max-height 300px.

**positions/**
- `ActivePositions.tsx` - Dense table of open positions with unrealized P&L, SL distance warnings, expandable detail rows

### `src/hooks/` - Custom Hooks
- `useWebSocket.ts` - WebSocket connection to `ws://localhost:8080/ws`. Auto-reconnect. Subscribes to all 5 index symbols on connect. Handles `signal:new` and `signal:updated` (dedup updates). Exported `subscribeSymbols()` helper.

### `src/lib/` - Utilities
- `api.ts` - REST client: trades, signals, positions, agent, risk, market data, strategies. Key functions: `getAllPrices()`, `refreshQuotes()`, `searchSymbols()`, `toggleYolo()`, `evaluateStrategyBatch()`, `toggleAutoMode()`, `updateStrategy()`
- `types.ts` - TypeScript interfaces for all entities (Trade, Signal, Position, AgentStatus with `yolo_mode` + `autonomy_level`)
- `formatters.ts` - INR currency (Indian number system: lakhs/crores), percentages, IST datetime
- `constants.ts` - `SYMBOLS` (5 indices), `STRATEGY_LABELS`, `STATUS_COLORS`, `WS_URL` (ws://localhost:8080/ws)

### `src/store/` - Zustand State
- `index.ts` - Single store with slices: prices (per symbol), positions, signals, scan logs, risk metrics, agent status, market status. `updatePrice()` action used by Header polling + WebSocket events. Signal actions: `addSignal()` (dedupes by id), `updateSignal()`, `removeSignal()`. `ScanLogEntry` type for manual scan feed.

## Theme — Institutional Terminal (Dark Only)

Bloomberg-inspired hedge fund terminal aesthetic. Dense, monospace-forward, warm amber accent.

```
Background:  #06060b  (--bg-primary)
Surface:     #0b0b13  (--bg-secondary)
Tertiary:    #12121c  (--bg-tertiary)
Elevated:    #181825  (--bg-elevated)
Border:      #1a1a2a
Profit:      #00e68a  (green)
Loss:        #ff4060  (red)
Accent:      #d4a843  (warm amber/gold — institutional)
Warning:     #f59e0b  (amber, YOLO badge)
Font:        Geist Sans + Geist Mono
```

### Design Principles
- **Density first** — tight padding (py-1.5, px-3), compact rows, minimal whitespace
- **Monospace-forward** — all data, labels, prices, timestamps in `font-mono`
- **Sharp corners** — `rounded` (4px) instead of `rounded-lg` (8px)
- **Font sizes** — 10px labels/badges, 12px (text-xs) data and primary text, text-sm for key values
- **Uppercase tracking** — section headers use `uppercase tracking-wider font-mono`
- **Dividers over cards** — inline dividers (`w-px h-4 bg-border`) separate metrics
- **Subtle depth** — noise texture overlay (`noise-bg`), glow utilities (`glow-profit`, `glow-loss`, `glow-accent`)
- **Amber accent** — warm gold (#d4a843) for active states, badges, selections — replaces generic indigo

### CSS Utilities (globals.css)
- `.noise-bg` — subtle fractal noise texture overlay (opacity 0.015)
- `.glow-profit` / `.glow-loss` / `.glow-accent` — colored box-shadow glows
- `.animate-fade-in` — 200ms fade-in with translateY for expanded panels

### Layout Dimensions
- Sidebar: 48px wide (`w-12`)
- Header: 36px tall (`h-9`)
- Main content offset: `ml-12 mt-9 p-3`

## Conventions
- All pages are client components (`'use client'`)
- All API calls go through `lib/api.ts` — never raw fetch in components
- WebSocket events go through `hooks/useWebSocket.ts`
- Currency formatted as INR with Indian number system (e.g., Rs 1,50,000)
- All 5 indices always referenced: NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY
- Backend API base URL: `http://localhost:8080/api/v1`
- WebSocket URL: `ws://localhost:8080/ws`
- Strategy badges: `text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent`
- Section headers: `text-xs font-mono font-medium text-text-secondary uppercase tracking-wider`
- Empty states: `text-xs font-mono text-text-muted` with lowercase text

## How-To Guides

### Add a New Page
1. Create `src/app/{page-name}/page.tsx` with `'use client'`
2. Add nav link in `src/components/layout/Sidebar.tsx`
3. Create domain components in `src/components/{domain}/`
4. Add store slice in `src/store/index.ts` if page needs its own state

### Add a New Component
1. Place in `src/components/{domain}/` matching the page domain
2. Use Tailwind with theme variables (--bg-primary, --profit, --loss, --accent, etc.)
3. Follow terminal aesthetic: monospace text, tight padding, sharp corners, uppercase headers
4. Get data from Zustand store or pass as props — don't fetch inside components
5. Use `formatINR()`, `formatPercent()` from `lib/formatters.ts`

### Add a New API Call
1. Add function in `src/lib/api.ts`
2. Add TypeScript types in `src/lib/types.ts`
3. Call from component or store action — not directly from hooks

### Add a New WebSocket Event
1. Add event type in `src/hooks/useWebSocket.ts` handler
2. Add store action in `src/store/index.ts` to process the event
3. Backend must publish to Redis channel for the event to flow through
