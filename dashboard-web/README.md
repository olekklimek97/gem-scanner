# Gem Scanner Dashboard (Next.js)

Real-time dashboard for the gem-scanner pipeline. Talks to the Flask backend
(`dashboard_local.py`) which serves JSON over HTTP on port 8420. Built with
Next.js 14 App Router, TypeScript, Tailwind, SWR, and Recharts.

## Prerequisites

- Node.js 18.18+ (or 20.x — both work with Next.js 14)
- The Flask backend running on `http://localhost:8420` — start it from the
  project root with `python dashboard_local.py`. Without the backend the
  dashboard renders but every panel shows a "backend unreachable" state.

## Setup

```bash
cd dashboard-web
npm install
cp .env.local.example .env.local        # optional, default URL is fine
```

## Develop

```bash
npm run dev
```

Opens on http://localhost:3000. Auto-reloads on file changes. SWR refetches
every 30 seconds; the timestamp in the header reflects the refresh cycle.

## Production build

```bash
npm run build
npm start                                # serves on :3000
```

## Type check

```bash
npm run typecheck                        # tsc --noEmit, no UI changes
```

## Environment

| Variable               | Default                 | Purpose                                           |
| ---------------------- | ----------------------- | ------------------------------------------------- |
| `NEXT_PUBLIC_API_URL`  | `http://localhost:8420` | Base URL of the Flask backend's JSON endpoints.   |

The URL is read at **build time** by Next.js (NEXT_PUBLIC_* are inlined), so
change it in `.env.local` before `npm run build` for production.

## Endpoints consumed

All from the Flask backend:

| Endpoint               | Used by                          |
| ---------------------- | -------------------------------- |
| `/api/positions`       | `PositionsTable`, `HeroMetrics`  |
| `/api/trades?limit=N`  | `RecentTrades`                   |
| `/api/system-status`   | `SystemHealth`, `HeroMetrics`    |
| `/api/metrics-summary` | `HeroMetrics`                    |
| `/api/history?limit=N` | `ScanHistoryChart`               |

The legacy HTML dashboard at `http://localhost:8420/` and the scan-history
page at `http://localhost:8420/history` remain available as a fallback.

## Layout

```
src/
├── app/
│   ├── layout.tsx        Root layout, font setup (Space Grotesk / Fraunces / JetBrains Mono)
│   ├── providers.tsx     SWR config provider (client component)
│   ├── page.tsx          Single-page dashboard
│   └── globals.css       Design tokens, dotted-grid background, base styles
├── components/
│   ├── Header.tsx        Title bar, live badge, refresh timestamp
│   ├── HeroMetrics.tsx   5 cards across the top
│   ├── SystemHealth.tsx  3 status pills + activity facts
│   ├── PositionsTable.tsx
│   ├── RecentTrades.tsx  Auto-scrolling event log
│   ├── ScanHistoryChart.tsx  Recharts line chart + expandable list
│   └── Footer.tsx
├── lib/
│   ├── api.ts            SWR hooks + raw fetchers (typed)
│   └── format.ts         Number / time formatting helpers
└── types/
    └── index.ts          Backend JSON shapes
```

## Design system

Tokens are defined in **two places** that must stay in sync:

- `tailwind.config.ts` — used by class-based styling
- `src/app/globals.css` — exposed as CSS variables for Recharts and inline styles

Color palette:

| Name        | Value     | Usage                                  |
| ----------- | --------- | -------------------------------------- |
| `bg`        | `#0a0e0a` | Page background                        |
| `bg-alt`    | `#0f1410` | Card backgrounds                       |
| `ink`       | `#e8f0d8` | Body text                              |
| `ink-dim`   | `#8a9686` | Secondary text / labels                |
| `green`     | `#6dd366` | Healthy state, profit, brand accent    |
| `amber`     | `#f4b942` | Partial / cascade-in-progress states   |
| `red`       | `#ff5b5b` | Errors, loss, stop-loss events         |
| `blue`      | `#5cc8ff` | Links, external nav                    |
| `magenta`   | `#ff6bd6` | Reserved (not currently used)          |
| `line`      | `#1c2418` | 1px borders                            |
