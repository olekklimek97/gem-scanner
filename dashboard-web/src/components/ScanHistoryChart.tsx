'use client';

import { useState, useMemo } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { useScanHistory } from '@/lib/api';
import { formatTimestamp } from '@/lib/format';

/**
 * Recharts line chart of scans over time (x = scan timestamp, y = token count).
 * Below the chart, an expandable list of the most-recent 10 scans.
 */
export function ScanHistoryChart() {
  const { data, error } = useScanHistory(50);
  const [expanded, setExpanded] = useState(false);

  // Chronological order for the chart (oldest → newest left-to-right)
  const chartData = useMemo(() => {
    if (!data) return [];
    return [...data]
      .reverse()
      .map((s) => ({
        // X tick label — keep compact
        label: new Date(s.timestamp).toLocaleString(undefined, {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
        }),
        tokens: s.total_tokens_found,
        topScore: s.top_score,
        id: s.id,
        timestamp: s.timestamp,
      }));
  }, [data]);

  const recent = (data ?? []).slice(0, 10);

  return (
    <section className="mb-10">
      <div className="mb-3 flex items-end justify-between">
        <h2 className="label-mono">Scan history</h2>
        <span className="label-mono">{data?.length ?? 0} scans</span>
      </div>

      <div className="line-card p-5">
        {error && (
          <div className="text-red label-mono">
            Failed to load scan history · {error.message ?? 'unknown error'}
          </div>
        )}

        {!error && chartData.length === 0 && (
          <div className="label-mono py-12 text-center">
            No scans recorded yet
          </div>
        )}

        {!error && chartData.length > 0 && (
          <div style={{ width: '100%', height: 240 }}>
            <ResponsiveContainer>
              <LineChart
                data={chartData}
                margin={{ top: 10, right: 10, left: -10, bottom: 0 }}
              >
                <CartesianGrid stroke="var(--line)" strokeDasharray="2 4" />
                <XAxis
                  dataKey="label"
                  stroke="var(--ink-dim)"
                  fontSize={10}
                  tickLine={false}
                  axisLine={{ stroke: 'var(--line)' }}
                />
                <YAxis
                  stroke="var(--ink-dim)"
                  fontSize={10}
                  tickLine={false}
                  axisLine={{ stroke: 'var(--line)' }}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-alt)',
                    border: '1px solid var(--line)',
                    borderRadius: '4px',
                    fontFamily: 'var(--font-jetbrains-mono), monospace',
                    fontSize: 11,
                  }}
                  labelStyle={{ color: 'var(--ink-dim)' }}
                  itemStyle={{ color: 'var(--ink)' }}
                />
                <Line
                  type="monotone"
                  dataKey="tokens"
                  stroke="var(--green)"
                  strokeWidth={2}
                  dot={{ r: 2, fill: 'var(--green)' }}
                  activeDot={{ r: 4 }}
                  name="Tokens scored"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {recent.length > 0 && (
          <div className="mt-5">
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="label-mono text-blue hover:underline"
            >
              {expanded ? '▼ Hide' : '▶ Last 10 scans'}
            </button>
            {expanded && (
              <table className="mt-3 w-full border-collapse">
                <thead>
                  <tr className="border-b border-line">
                    <th className="label-mono px-3 py-2 text-left">Date</th>
                    <th className="label-mono px-3 py-2 text-right">Tokens</th>
                    <th className="label-mono px-3 py-2 text-right">Top score</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((s) => {
                    const color =
                      s.top_score >= 75
                        ? 'var(--green)'
                        : s.top_score >= 55
                          ? 'var(--amber)'
                          : 'var(--red)';
                    return (
                      <tr key={s.id} className="border-b border-line">
                        <td className="px-3 py-2 font-mono text-sm">
                          {formatTimestamp(s.timestamp)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-sm">
                          {s.total_tokens_found}
                        </td>
                        <td className="px-3 py-2 text-right">
                          <span
                            className="border px-2 py-0.5 label-mono !text-[10px]"
                            style={{ borderColor: color, color }}
                          >
                            {s.top_score}/100
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
