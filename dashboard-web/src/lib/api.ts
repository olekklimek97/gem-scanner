'use client';

import useSWR, { type SWRConfiguration, type SWRResponse } from 'swr';
import type {
  Position,
  Trade,
  SystemStatus,
  MetricsSummary,
  ScanHistoryEntry,
} from '@/types';

/** Flask backend base URL. Overridable via NEXT_PUBLIC_API_URL. */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '') || 'http://localhost:8420';

const REFRESH_MS = 30_000;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

/**
 * Plain JSON fetcher. Returns `unknown` and relies on the call site (or
 * `useSWR<T>`) to assert the response shape. Throws ApiError on non-2xx
 * with the backend's error message if present (Flask returns {error: …}).
 */
async function fetcher(path: string): Promise<unknown> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { error?: string };
      if (body?.error) detail = body.error;
    } catch {
      /* ignore — non-JSON error */
    }
    throw new ApiError(res.status, `GET ${path} failed (${res.status}): ${detail}`);
  }
  return res.json();
}

const swrOpts: SWRConfiguration = {
  refreshInterval: REFRESH_MS,
  revalidateOnFocus: true,
  keepPreviousData: true,
};

// ─── Typed SWR hooks ───────────────────────────────────────────────────────
// Each declares the response shape via useSWR's generic param.

export function usePositions(): SWRResponse<Position[]> {
  return useSWR<Position[]>('/api/positions', fetcher, swrOpts);
}

export function useTrades(limit = 50): SWRResponse<Trade[]> {
  return useSWR<Trade[]>(`/api/trades?limit=${limit}`, fetcher, swrOpts);
}

export function useSystemStatus(): SWRResponse<SystemStatus> {
  return useSWR<SystemStatus>('/api/system-status', fetcher, swrOpts);
}

export function useMetricsSummary(): SWRResponse<MetricsSummary> {
  return useSWR<MetricsSummary>('/api/metrics-summary', fetcher, swrOpts);
}

export function useScanHistory(limit = 50): SWRResponse<ScanHistoryEntry[]> {
  return useSWR<ScanHistoryEntry[]>(`/api/history?limit=${limit}`, fetcher, swrOpts);
}

// ─── Raw fetchers (for non-SWR call sites) ────────────────────────────────
export const api = {
  positions: () => fetcher('/api/positions') as Promise<Position[]>,
  trades: (limit = 50) => fetcher(`/api/trades?limit=${limit}`) as Promise<Trade[]>,
  systemStatus: () => fetcher('/api/system-status') as Promise<SystemStatus>,
  metricsSummary: () => fetcher('/api/metrics-summary') as Promise<MetricsSummary>,
  scanHistory: (limit = 50) =>
    fetcher(`/api/history?limit=${limit}`) as Promise<ScanHistoryEntry[]>,
};
