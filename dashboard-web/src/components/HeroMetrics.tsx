'use client';

import { useMetricsSummary, useSystemStatus } from '@/lib/api';
import { formatSol } from '@/lib/format';

/**
 * 5 hero cards across the top. Pulls from both metrics-summary (counts +
 * win rate) and system-status (total PnL). If either errors, the affected
 * cards show "—" rather than crashing the whole panel.
 */
export function HeroMetrics() {
  const metrics = useMetricsSummary();
  const status = useSystemStatus();

  const cards = [
    {
      label: 'Total positions',
      value: metrics.data?.total_positions ?? '—',
      accent: 'ink' as const,
    },
    {
      label: 'Active',
      value: metrics.data?.active_positions ?? '—',
      accent: 'green' as const,
    },
    {
      label: 'Closed',
      value: metrics.data?.closed_positions ?? '—',
      accent: 'ink' as const,
    },
    {
      label: 'Win rate',
      value:
        metrics.data?.win_rate_pct !== undefined
          ? `${metrics.data.win_rate_pct.toFixed(1)}%`
          : '—',
      accent: 'amber' as const,
    },
    {
      label: 'Total PnL · SOL',
      value:
        status.data?.total_pnl_sol !== undefined
          ? `${status.data.total_pnl_sol >= 0 ? '+' : ''}${formatSol(status.data.total_pnl_sol)}`
          : '—',
      accent:
        status.data?.total_pnl_sol !== undefined && status.data.total_pnl_sol < 0
          ? ('red' as const)
          : ('green' as const),
    },
  ];

  return (
    <section className="mb-10">
      <h2 className="label-mono mb-3">Overview</h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {cards.map((c) => (
          <Card key={c.label} label={c.label} value={c.value} accent={c.accent} />
        ))}
      </div>
    </section>
  );
}

interface CardProps {
  label: string;
  value: string | number;
  accent: 'ink' | 'green' | 'red' | 'amber' | 'blue';
}

function Card({ label, value, accent }: CardProps) {
  const colorVar: Record<CardProps['accent'], string> = {
    ink: 'var(--ink)',
    green: 'var(--green)',
    red: 'var(--red)',
    amber: 'var(--amber)',
    blue: 'var(--blue)',
  };
  return (
    <div className="line-card p-5">
      <div
        className="display-number text-4xl md:text-5xl"
        style={{ color: colorVar[accent] }}
      >
        {value}
      </div>
      <div className="label-mono mt-3">{label}</div>
    </div>
  );
}
