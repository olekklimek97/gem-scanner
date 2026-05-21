'use client';

import { useEffect, useState } from 'react';

/**
 * Top of the page. Live badge with pulsing dot, big title with italic green
 * "Dashboard", and a meta row with deployment info + last-refresh time.
 *
 * lastRefreshAt comes in via prop so the parent owns the refresh tick — that
 * way every panel re-rendering doesn't independently update the timestamp.
 */
interface HeaderProps {
  lastRefreshAt: Date | null;
  mode?: 'dry-run' | 'live';
}

export function Header({ lastRefreshAt, mode = 'dry-run' }: HeaderProps) {
  // Defer rendering the timestamp until client mount to avoid an SSR/CSR
  // mismatch warning (server time != client time).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const refreshLabel = mounted && lastRefreshAt
    ? lastRefreshAt.toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : '—';

  return (
    <header className="mb-10">
      <div className="inline-flex items-center gap-2 border border-line bg-bg-alt px-3 py-1.5">
        <span className="live-dot" aria-hidden />
        <span className="label-mono !text-[10px] text-ink">
          Live · gem-scanner · AWS
        </span>
      </div>

      <h1 className="display-number mt-5 text-5xl md:text-6xl text-ink">
        Gem Scanner{' '}
        <span
          className="italic"
          style={{ color: 'var(--green)' }}
        >
          Dashboard
        </span>
      </h1>

      <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 label-mono">
        <span>Solana liftoff sniper + auto-trader</span>
        <span className="text-line" aria-hidden>
          •
        </span>
        <span>Mode · {mode}</span>
        <span className="text-line" aria-hidden>
          •
        </span>
        <span>Refreshed · {refreshLabel}</span>
      </div>
    </header>
  );
}
