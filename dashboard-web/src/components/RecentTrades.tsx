'use client';

import { useEffect, useRef } from 'react';
import { useTrades } from '@/lib/api';
import { formatTime, formatSol } from '@/lib/format';
import type { Trade } from '@/types';

const MAX_VISIBLE = 20;

// Icon + accent color per action. Anything not in this map gets the default
// neutral row.
const ACTION_STYLES: Record<
  string,
  { icon: string; color: string; label: string }
> = {
  buy: { icon: '🟢', color: 'var(--green)', label: 'BUY' },
  manual_buy: { icon: '🟢', color: 'var(--green)', label: 'MANUAL BUY' },
  cascade_tp: { icon: '🟡', color: 'var(--amber)', label: 'CASCADE TP' },
  tp1: { icon: '✅', color: 'var(--green)', label: 'TP1' },
  stop_loss: { icon: '🔴', color: 'var(--red)', label: 'STOP LOSS' },
  manual_sell: { icon: '📤', color: 'var(--blue)', label: 'MANUAL SELL' },
  dust_close: { icon: '💀', color: 'var(--ink-dim)', label: 'DUST CLOSE' },
  emergency_sell: { icon: '🚨', color: 'var(--red)', label: 'EMERGENCY SELL' },
  emergency_sell_trigger: { icon: '🚨', color: 'var(--red)', label: 'EMERGENCY' },
  skipped_duplicate: { icon: '⏭️', color: 'var(--ink-dim)', label: 'SKIPPED DUP' },
  safety_event: { icon: '⚠️', color: 'var(--amber)', label: 'SAFETY' },
  withdraw: { icon: '💸', color: 'var(--blue)', label: 'WITHDRAW' },
  auto_sweep: { icon: '🧹', color: 'var(--blue)', label: 'AUTO SWEEP' },
  warning: { icon: '⚠️', color: 'var(--amber)', label: 'WARNING' },
};

function defaultStyle(action: string) {
  return { icon: '📋', color: 'var(--ink-dim)', label: action.toUpperCase() };
}

export function RecentTrades() {
  const { data, error, isLoading } = useTrades(50);
  const listRef = useRef<HTMLDivElement | null>(null);
  const lastFirstTimestamp = useRef<string | null>(null);

  // Auto-scroll to top whenever a new event lands at index 0. Compare against
  // the previous head timestamp so a refresh that returns the same data
  // doesn't yank the scroll position.
  useEffect(() => {
    if (!data || data.length === 0) return;
    const head = data[0].timestamp;
    if (head !== lastFirstTimestamp.current) {
      lastFirstTimestamp.current = head;
      listRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }, [data]);

  const visible = (data ?? []).slice(0, MAX_VISIBLE);

  return (
    <section className="mb-10">
      <div className="mb-3 flex items-end justify-between">
        <h2 className="label-mono">Recent trades</h2>
        <span className="label-mono">{data?.length ?? 0} events</span>
      </div>

      <div className="line-card">
        {error && (
          <div className="p-5 text-red label-mono">
            Failed to load trades · {error.message ?? 'unknown error'}
          </div>
        )}

        <div
          ref={listRef}
          className="max-h-[420px] overflow-y-auto themed-scroll"
        >
          {isLoading && visible.length === 0 && (
            <div className="p-6 label-mono text-center">Loading…</div>
          )}
          {!isLoading && visible.length === 0 && !error && (
            <div className="p-6 label-mono text-center">No trades yet</div>
          )}
          {visible.map((t, i) => (
            <TradeRow key={`${t.timestamp}-${i}`} trade={t} />
          ))}
        </div>
      </div>
    </section>
  );
}

function TradeRow({ trade }: { trade: Trade }) {
  const s = ACTION_STYLES[trade.action] ?? defaultStyle(trade.action);

  // Build a single-line description with whatever salient fields the event has.
  const parts: string[] = [];
  if (trade.token) parts.push(trade.token);
  if (typeof trade.score === 'number') parts.push(`SCORE ${trade.score}`);
  if (typeof trade.new_alert_score === 'number') {
    parts.push(`NEW ${trade.new_alert_score}`);
  }
  if (typeof trade.amount_sol === 'number') {
    parts.push(`${formatSol(trade.amount_sol)} SOL`);
  }
  if (typeof trade.level === 'number') parts.push(`#${trade.level}`);
  if (typeof trade.pnl_pct === 'number') {
    parts.push(`PnL ${trade.pnl_pct >= 0 ? '+' : ''}${trade.pnl_pct.toFixed(0)}%`);
  }
  if (typeof trade.sell_pct === 'number') {
    parts.push(`SOLD ${trade.sell_pct.toFixed(0)}%`);
  }
  if (trade.existing_status) parts.push(`was ${trade.existing_status}`);

  return (
    <div className="flex items-center gap-3 border-b border-line px-4 py-2.5 last:border-b-0">
      <span className="text-base" aria-hidden>
        {s.icon}
      </span>
      <span
        className="label-mono !text-[10px] min-w-[80px]"
        style={{ color: s.color }}
      >
        {s.label}
      </span>
      <span className="font-mono text-sm text-ink flex-1 truncate">
        {parts.join(' · ') || '—'}
      </span>
      <span className="label-mono !text-[10px] text-ink-dim whitespace-nowrap">
        {formatTime(trade.timestamp)}
      </span>
    </div>
  );
}
