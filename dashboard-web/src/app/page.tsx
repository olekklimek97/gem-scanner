'use client';

import { useEffect, useState } from 'react';
import { Header } from '@/components/Header';
import { HeroMetrics } from '@/components/HeroMetrics';
import { SystemHealth } from '@/components/SystemHealth';
import { PositionsTable } from '@/components/PositionsTable';
import { RecentTrades } from '@/components/RecentTrades';
import { ScanHistoryChart } from '@/components/ScanHistoryChart';
import { Footer } from '@/components/Footer';

/**
 * Single-page dashboard. Refresh timestamp is owned here and bumped every
 * 30 seconds so the header reflects the SWR cycle. Sections each subscribe
 * to their own SWR keys via hooks in lib/api.ts.
 */
export default function HomePage() {
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  useEffect(() => {
    setLastRefresh(new Date());
    const id = setInterval(() => setLastRefresh(new Date()), 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <main className="mx-auto max-w-[1280px] px-6 py-10 md:px-10 md:py-14">
      <Header lastRefreshAt={lastRefresh} />
      <HeroMetrics />
      <SystemHealth />
      <PositionsTable />
      <RecentTrades />
      <ScanHistoryChart />
      <Footer />
    </main>
  );
}
