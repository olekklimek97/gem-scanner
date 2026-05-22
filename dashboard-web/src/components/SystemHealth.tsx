'use client';

import { useSystemStatus } from '@/lib/api';
import { formatTimestamp } from '@/lib/format';

/**
 * 3 status pills (Sniper / Trading Bot / Dashboard) and 3 facts about
 * recent activity. We can't directly observe whether the sniper/bot
 * processes are running, but we use observable proxies:
 *   Sniper:   "online" if the most-recent sniper-alert file is < 15 min old
 *   Bot:      "online" if positions.json was touched recently (we use
 *             system-status reachability as a soft proxy — Flask serves both)
 *   Dashboard: always online (we're rendering)
 */
export function SystemHealth() {
  const { data, error, isLoading } = useSystemStatus();

  // Sniper liveness: last alert mtime within the last 15 minutes
  const sniperOnline = (() => {
    if (!data?.last_alert_time) return false;
    const t = new Date(data.last_alert_time).getTime();
    if (Number.isNaN(t)) return false;
    return Date.now() - t < 15 * 60_000;
  })();

  // Bot liveness: we have to trust that if the dashboard's Flask backend
  // is reachable AND there are open positions OR alerts being consumed,
  // the bot is alive. Best proxy: backend reachable.
  const botOnline = !error && data !== undefined;

  return (
    <section className="mb-10">
      <h2 className="label-mono mb-3">System health</h2>
      <div className="line-card grid grid-cols-1 gap-6 p-5 md:grid-cols-2 lg:grid-cols-3">
        <div className="flex flex-wrap items-center gap-3">
          <StatusPill label="Sniper" online={sniperOnline} loading={isLoading} />
          <StatusPill label="Trading bot" online={botOnline} loading={isLoading} />
          <StatusPill label="Dashboard" online={true} />
        </div>

        <Fact label="Last scan" value={formatTimestamp(data?.latest_scan_time)} />
        <Fact
          label="Sniper alerts on disk"
          value={data?.sniper_alerts_count?.toLocaleString() ?? '—'}
          sub={data?.last_alert_time ? `Last · ${formatTimestamp(data.last_alert_time)}` : ''}
        />
      </div>

      {error && (
        <div className="mt-3 label-mono text-red">
          Backend unreachable · is Flask running on :8420?
        </div>
      )}
    </section>
  );
}

interface StatusPillProps {
  label: string;
  online: boolean;
  loading?: boolean;
}

function StatusPill({ label, online, loading }: StatusPillProps) {
  const color = loading ? 'var(--ink-dim)' : online ? 'var(--green)' : 'var(--red)';
  return (
    <div
      className="inline-flex items-center gap-2 border bg-bg px-3 py-1.5"
      style={{ borderColor: color }}
    >
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{
          background: color,
          boxShadow: online ? `0 0 8px ${color}` : 'none',
        }}
        aria-hidden
      />
      <span className="label-mono !text-[10px]" style={{ color }}>
        {label} · {loading ? 'checking' : online ? 'online' : 'offline'}
      </span>
    </div>
  );
}

function Fact({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="label-mono">{label}</div>
      <div className="font-mono mt-1 text-ink">{value}</div>
      {sub && <div className="font-mono mt-0.5 text-xs text-ink-dim">{sub}</div>}
    </div>
  );
}
