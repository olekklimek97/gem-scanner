'use client';

import { SWRConfig } from 'swr';
import type { ReactNode } from 'react';

/**
 * Global SWR config. Individual hooks in lib/api.ts each set their own
 * refresh interval (30s), so we only set baseline behavior here.
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <SWRConfig
      value={{
        // Don't error-retry forever on a dead backend — that thrashes the UI.
        // 3 attempts is enough to ride out a Flask restart.
        errorRetryCount: 3,
        errorRetryInterval: 4_000,
        // Show stale data while revalidating so panels don't flicker to empty.
        keepPreviousData: true,
      }}
    >
      {children}
    </SWRConfig>
  );
}
