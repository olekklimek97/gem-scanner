'use client';

import { usePositions } from '@/lib/api';
import { formatHours, formatPnl, shortAddress } from '@/lib/format';
import type { Position } from '@/types';

const MAX_VISIBLE = 20;

/**
 * Live positions table. Shows top 20 by buy_time (newest first). Each row
 * gets a colored left border based on PnL:
 *   green  → in profit
 *   red    → in loss
 *   amber  → partial (mid-cascade)
 *
 * Status badges call out moonbag tier (cascade_level >= 1).
 */
export function PositionsTable() {
  const { data, error, isLoading } = usePositions();

  const positions = (data ?? [])
    // Show open / partial first, then closed / stopped / dust
    .slice()
    .sort((a, b) => {
      const order: Record<string, number> = {
        open: 0,
        partial: 1,
        closed: 2,
        dust: 3,
        stopped: 4,
      };
      const ao = order[a.status] ?? 9;
      const bo = order[b.status] ?? 9;
      if (ao !== bo) return ao - bo;
      // Within same status, newest first
      const at = new Date(a.buy_time).getTime() || 0;
      const bt = new Date(b.buy_time).getTime() || 0;
      return bt - at;
    });

  const visible = positions.slice(0, MAX_VISIBLE);
  const hasMore = positions.length > MAX_VISIBLE;

  return (
    <section className="mb-10">
      <div className="mb-3 flex items-end justify-between">
        <h2 className="label-mono">Live positions</h2>
        <span className="label-mono">
          {positions.length} total
          {hasMore && ' · showing 20'}
        </span>
      </div>

      <div className="line-card overflow-x-auto themed-scroll">
        {error && (
          <div className="p-5 text-red label-mono">
            Failed to load positions · {error.message ?? 'unknown error'}
          </div>
        )}

        {!error && (
          <table className="w-full min-w-[800px] border-collapse">
            <thead>
              <tr className="border-b border-line">
                <Th>Token</Th>
                <Th align="right">Score</Th>
                <Th>Status</Th>
                <Th align="right">Cascade</Th>
                <Th align="right">PnL %</Th>
                <Th align="right">Time held</Th>
                <Th align="right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {isLoading && visible.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-8 text-center label-mono">
                    Loading…
                  </td>
                </tr>
              )}
              {!isLoading && visible.length === 0 && (
                <tr>
                  <td colSpan={7} className="p-8 text-center label-mono">
                    No active positions — bot is waiting for sniper signals
                  </td>
                </tr>
              )}
              {visible.map((p) => (
                <Row key={p.token_address} position={p} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {hasMore && (
        <div className="mt-3 label-mono text-right">
          {positions.length - MAX_VISIBLE} more · view all (coming)
        </div>
      )}
    </section>
  );
}

function Th({
  children,
  align = 'left',
}: {
  children: React.ReactNode;
  align?: 'left' | 'right';
}) {
  return (
    <th
      className="label-mono px-4 py-3"
      style={{ textAlign: align }}
    >
      {children}
    </th>
  );
}

function Row({ position: p }: { position: Position }) {
  // Pick a left-accent color from PnL and status
  const accent =
    p.status === 'partial'
      ? 'var(--amber)'
      : p.pnl_pct > 0
        ? 'var(--green)'
        : p.pnl_pct < 0
          ? 'var(--red)'
          : 'var(--ink-dim)';

  return (
    <tr
      className="border-b border-line transition-colors hover:bg-bg-alt"
      style={{ borderLeft: `2px solid ${accent}` }}
    >
      <td className="px-4 py-3">
        <div className="font-medium text-ink">
          {p.token_name}{' '}
          <span className="text-ink-dim">${p.token_symbol}</span>
        </div>
        <div className="font-mono text-[11px] text-ink-dim">
          {shortAddress(p.token_address)}
        </div>
      </td>
      <td className="px-4 py-3 text-right font-mono">{p.score}</td>
      <td className="px-4 py-3">
        <StatusBadge status={p.status} cascade={p.cascade_level} />
      </td>
      <td className="px-4 py-3 text-right font-mono">{p.cascade_level}</td>
      <td
        className="px-4 py-3 text-right font-mono"
        style={{ color: accent }}
      >
        {formatPnl(p.pnl_pct)}
        {p.pnl_is_live && (
          <span className="ml-1 label-mono !text-[9px] text-ink-dim">live</span>
        )}
      </td>
      <td className="px-4 py-3 text-right font-mono text-ink-dim">
        {formatHours(p.time_held_hours)}
      </td>
      <td className="px-4 py-3 text-right">
        {p.url ? (
          <a
            href={p.url}
            target="_blank"
            rel="noreferrer noopener"
            className="label-mono text-blue hover:underline"
          >
            DEX ↗
          </a>
        ) : (
          <span className="label-mono text-ink-dim">—</span>
        )}
      </td>
    </tr>
  );
}

function StatusBadge({ status, cascade }: { status: string; cascade: number }) {
  const map: Record<string, { color: string; label: string }> = {
    open: { color: 'var(--green)', label: cascade >= 1 ? `MOONBAG ${cascade}` : 'OPEN' },
    partial: { color: 'var(--amber)', label: `PARTIAL ${cascade}` },
    closed: { color: 'var(--ink-dim)', label: 'CLOSED' },
    dust: { color: 'var(--ink-dim)', label: 'DUST' },
    stopped: { color: 'var(--red)', label: 'STOPPED' },
  };
  const m = map[status] ?? { color: 'var(--ink-dim)', label: status.toUpperCase() };
  return (
    <span
      className="inline-block border px-2 py-0.5 label-mono !text-[10px]"
      style={{ borderColor: m.color, color: m.color }}
    >
      {m.label}
    </span>
  );
}
