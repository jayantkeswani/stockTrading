# Graph Report - frontend  (2026-04-24)

## Corpus Check
- Corpus is ~23,586 words - fits in a single context window. You may not need a graph.

## Summary
- 268 nodes · 341 edges · 18 communities detected
- Extraction: 88% EXTRACTED · 11% INFERRED · 1% AMBIGUOUS · INFERRED: 37 edges (avg confidence: 0.79)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Charts & Price Display|Charts & Price Display]]
- [[_COMMUNITY_Frontend Conventions & Patterns|Frontend Conventions & Patterns]]
- [[_COMMUNITY_IST DateTime Utilities|IST Date/Time Utilities]]
- [[_COMMUNITY_Types, Constants & State Persistence|Types, Constants & State Persistence]]
- [[_COMMUNITY_Research & Trades Components|Research & Trades Components]]
- [[_COMMUNITY_Formatting & Display Helpers|Formatting & Display Helpers]]
- [[_COMMUNITY_Project Docs & Agent Guidelines|Project Docs & Agent Guidelines]]
- [[_COMMUNITY_App Shell & Layout|App Shell & Layout]]
- [[_COMMUNITY_Research Sub-Agents (6 specialists)|Research Sub-Agents (6 specialists)]]
- [[_COMMUNITY_PnL Heatmap|PnL Heatmap]]
- [[_COMMUNITY_Signal Cards & Badges|Signal Cards & Badges]]
- [[_COMMUNITY_Auxiliary (34)|Auxiliary (34)]]
- [[_COMMUNITY_Auxiliary (48)|Auxiliary (48)]]
- [[_COMMUNITY_Auxiliary (49)|Auxiliary (49)]]
- [[_COMMUNITY_Auxiliary (50)|Auxiliary (50)]]
- [[_COMMUNITY_Auxiliary (51)|Auxiliary (51)]]
- [[_COMMUNITY_Auxiliary (52)|Auxiliary (52)]]
- [[_COMMUNITY_Auxiliary (53)|Auxiliary (53)]]

## God Nodes (most connected - your core abstractions)
1. `API Client (lib/api.ts)` - 20 edges
2. `formatters.ts - Frontend Utility Formatters` - 19 edges
3. `Constants (lib/constants.ts)` - 15 edges
4. `useStore - Zustand Store Hook (single store, persisted)` - 15 edges
5. `Zustand Global Store` - 14 edges
6. `toIST()` - 9 edges
7. `buildPreset()` - 8 edges
8. `Research Page` - 8 edges
9. `TypeScript Types (lib/types.ts)` - 8 edges
10. `Formatters (lib/formatters.ts)` - 8 edges

## Surprising Connections (you probably didn't know these)
- `formatINR() - Indian Rupee Currency Formatter` --semantically_similar_to--> `Color: Profit Green (#00e68a)`  [INFERRED] [semantically similar]
  frontend/src/lib/formatters.ts → frontend/CLAUDE.md
- `globe.svg - Globe/Web Icon (grey, 16x16)` --conceptually_related_to--> `Next.js 15 - App Router Framework`  [AMBIGUOUS]
  frontend/public/globe.svg → frontend/README.md
- `window.svg - Browser Window/App Icon (grey, 16x16)` --conceptually_related_to--> `Next.js 15 - App Router Framework`  [AMBIGUOUS]
  frontend/public/window.svg → frontend/README.md
- `file.svg - Document/File Icon (grey, 16x16)` --conceptually_related_to--> `Frontend README - Next.js Project Bootstrap Info`  [AMBIGUOUS]
  frontend/public/file.svg → frontend/README.md
- `next.svg - Next.js Wordmark Logo (black, 394x80)` --references--> `Next.js 15 - App Router Framework`  [INFERRED]
  frontend/public/next.svg → frontend/README.md

## Hyperedges (group relationships)
- **Research Flow: Search -> Progress -> Report -> History** — component_researchsearch, component_researchprogress, component_researchreport, component_reporthistory [EXTRACTED 0.95]
- **Trades Analysis Flow: PeriodFilter -> SummaryStrip -> TradesTable** — component_periodfilter, component_summarystrip, component_tradestable [EXTRACTED 0.95]
- **Signal Display Flow: SignalCard -> SignalTypeBadge -> ConfidenceFactorsBar** — component_signalcard, component_signaltypebadge, component_confidencefactorsbar [EXTRACTED 0.95]
- **Live P&L Computation Pipeline** — positions_activepositions, dashboard_pnlcard, store_zustand [INFERRED 0.85]
- **Signal Execution Flow** — dashboard_scannerpanel, dashboard_executesignalmodal, lib_api [EXTRACTED 0.95]
- **Real-Time WebSocket → Store → UI Update Pipeline** — hooks_usewebsocket, store_zustand, charts_pricechart [INFERRED 0.90]
- **Research Session Lifecycle: start, update agents, complete** — store_action_startresearchsession, store_action_updateresearchagent, store_action_completeresearch, store_slice_research [EXTRACTED 1.00]
- **IST Date Helpers: unified timezone-aware date manipulation for IST** — formatter_toistdate, formatter_startofdayist, formatter_endofdayist, formatter_startofmonthist, formatter_endofmonthist, formatter_startofweekist, ist_offset_const [EXTRACTED 1.00]
- **Persisted State Cluster: scanLogs + activeTimeframe survive page reload via Zustand persist** — store_persist_middleware, store_slice_scanlogs, store_slice_ui, localstorage_persistence [EXTRACTED 1.00]

## Communities

### Community 0 - "Charts & Price Display"
Cohesion: 0.12
Nodes (39): ChartModal Overlay, PriceChart TradingView Component, SymbolSelector Component (local), ToggleSwitch Component (local), AgentFeed Log Component, ExecuteSignalModal Pre-Trade Confirm, PnLCard Strip Component, QuickStats Component (+31 more)

### Community 1 - "Frontend Conventions & Patterns"
Cohesion: 0.09
Nodes (31): Convention: All API Calls via lib/api.ts Only, Convention: All Pages are Client Components (use client directive), Convention: All WebSocket Events via useWebSocket.ts, Color: Accent Amber/Gold (#d4a843) - Institutional Highlight, Color: Loss Red (#ff4060), Color: Profit Green (#00e68a), Design Principle: Density First - Tight Padding, Compact Rows, Design Theme: Institutional Terminal - Bloomberg-Inspired Hedge Fund Aesthetic (+23 more)

### Community 2 - "IST Date/Time Utilities"
Cohesion: 0.16
Nodes (17): eachDayInRange(), endOfDayIST(), endOfMonthIST(), formatINR(), formatINRCompact(), fromIST(), isoDateIST(), startOfDayIST() (+9 more)

### Community 3 - "Types, Constants & State Persistence"
Cohesion: 0.11
Nodes (21): lib/constants.ts - App Constants (SYMBOLS, STRATEGY_LABELS, WS_URL, Timeframe), lib/types.ts - TypeScript Interface Definitions, localStorage Persistence: scanLogs + activeTimeframe survive page reload, store/index.ts - Zustand Global App State Store, Zustand persist Middleware - Persists scanLogs + activeTimeframe to localStorage, ScanLogEntry - Type for Scan Feed Log Entries, Store Slice: closedToday - Today's Closed Trades, Store Slice: marketStatus - Market Open/Closed State (+13 more)

### Community 4 - "Research & Trades Components"
Cohesion: 0.17
Nodes (20): ActionableLevels Component, Markdown Component, PeriodFilter Component, ReportHistory Component, ResearchProgress Component, ResearchReport Component, ResearchSearch Component, SummaryStrip Component (+12 more)

### Community 5 - "Formatting & Display Helpers"
Cohesion: 0.14
Nodes (3): formatTime(), formatDate(), load()

### Community 6 - "Project Docs & Agent Guidelines"
Cohesion: 0.2
Nodes (10): Frontend AGENTS.md - Next.js Agent Usage Warning, Frontend README - Next.js Project Bootstrap Info, Next.js Agent Warning - Breaking Changes vs Training Data, Next.js 15 - App Router Framework, file.svg - Document/File Icon (grey, 16x16), globe.svg - Globe/Web Icon (grey, 16x16), next.svg - Next.js Wordmark Logo (black, 394x80), vercel.svg - Vercel Triangle Logo (white, 1155x1000) (+2 more)

### Community 7 - "App Shell & Layout"
Cohesion: 0.29
Nodes (4): AppShell(), subscribeSymbols(), useWebSocket(), loadWatchlist()

### Community 8 - "Research Sub-Agents (6 specialists)"
Cohesion: 0.29
Nodes (7): Research Sub-Agent: fundamental - Earnings & Growth Analysis, Research Sub-Agent: institutional - FII/DII/MF Holdings Analysis, Research Sub-Agent: news_sentiment - News & Sentiment Analysis, Research Sub-Agent: oi_derivatives - Open Interest Data Analysis, Research Sub-Agent: technical - Price Trends & Patterns Analysis, Research Sub-Agent: valuation - Valuation Metrics Analysis, Action: startResearchSession() - Initialize 6-Agent Research Session

### Community 9 - "PnL Heatmap"
Cohesion: 0.4
Nodes (2): inPeriod(), key()

### Community 13 - "Signal Cards & Badges"
Cohesion: 0.5
Nodes (5): ConfidenceFactorsBar Component (local), RecommendationBadge Component, SignalCard Component (local), SignalTypeBadge Component (local), WindowBadge Component (local)

### Community 34 - "Auxiliary (34)"
Cohesion: 1.0
Nodes (2): Root Layout, AppShell Component

### Community 48 - "Auxiliary (48)"
Cohesion: 1.0
Nodes (1): PostCSS Config

### Community 49 - "Auxiliary (49)"
Cohesion: 1.0
Nodes (1): ESLint Config

### Community 50 - "Auxiliary (50)"
Cohesion: 1.0
Nodes (1): Next.js Config

### Community 51 - "Auxiliary (51)"
Cohesion: 1.0
Nodes (1): Action: updatePrice() - Update Per-Symbol Price in Store

### Community 52 - "Auxiliary (52)"
Cohesion: 1.0
Nodes (1): Action: addSignal() - Add Signal with Deduplication by ID

### Community 53 - "Auxiliary (53)"
Cohesion: 1.0
Nodes (1): Action: addPosition() - Add Position with Deduplication by ID

## Ambiguous Edges - Review These
- `Frontend README - Next.js Project Bootstrap Info` → `file.svg - Document/File Icon (grey, 16x16)`  [AMBIGUOUS]
  frontend/public/file.svg · relation: conceptually_related_to
- `Next.js 15 - App Router Framework` → `globe.svg - Globe/Web Icon (grey, 16x16)`  [AMBIGUOUS]
  frontend/public/globe.svg · relation: conceptually_related_to
- `Next.js 15 - App Router Framework` → `window.svg - Browser Window/App Icon (grey, 16x16)`  [AMBIGUOUS]
  frontend/public/window.svg · relation: conceptually_related_to

## Knowledge Gaps
- **51 isolated node(s):** `Root Layout`, `AppShell Component`, `Markdown Component`, `WindowBadge Component (local)`, `ToggleSwitch Component (local)` (+46 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `PnL Heatmap`** (6 nodes): `cellBg()`, `getMonthsInRange()`, `inPeriod()`, `key()`, `mondayDow()`, `PnLHeatmap.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Auxiliary (34)`** (2 nodes): `Root Layout`, `AppShell Component`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Auxiliary (48)`** (1 nodes): `PostCSS Config`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Auxiliary (49)`** (1 nodes): `ESLint Config`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Auxiliary (50)`** (1 nodes): `Next.js Config`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Auxiliary (51)`** (1 nodes): `Action: updatePrice() - Update Per-Symbol Price in Store`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Auxiliary (52)`** (1 nodes): `Action: addSignal() - Add Signal with Deduplication by ID`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Auxiliary (53)`** (1 nodes): `Action: addPosition() - Add Position with Deduplication by ID`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Frontend README - Next.js Project Bootstrap Info` and `file.svg - Document/File Icon (grey, 16x16)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Next.js 15 - App Router Framework` and `globe.svg - Globe/Web Icon (grey, 16x16)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Next.js 15 - App Router Framework` and `window.svg - Browser Window/App Icon (grey, 16x16)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `useStore - Zustand Store Hook (single store, persisted)` connect `Types, Constants & State Persistence` to `Charts & Price Display`, `Research & Trades Components`?**
  _High betweenness centrality (0.124) - this node is a cross-community bridge._
- **Why does `store/index.ts - Zustand Global App State Store` connect `Types, Constants & State Persistence` to `Frontend Conventions & Patterns`?**
  _High betweenness centrality (0.084) - this node is a cross-community bridge._
- **Why does `Frontend CLAUDE.md - Frontend Architecture & Convention Guide` connect `Frontend Conventions & Patterns` to `Types, Constants & State Persistence`?**
  _High betweenness centrality (0.082) - this node is a cross-community bridge._
- **What connects `Root Layout`, `AppShell Component`, `Markdown Component` to the rest of the system?**
  _51 weakly-connected nodes found - possible documentation gaps or missing edges._