'use client';

export function Footer() {
  return (
    <footer className="mt-16 border-t border-line pt-6">
      <div className="flex flex-wrap items-center justify-between gap-3 label-mono">
        <span>v2.1 · architecture map</span>
        <a
          href="https://github.com/olekklimek97/gem-scanner"
          target="_blank"
          rel="noreferrer noopener"
          className="text-blue hover:underline"
        >
          github.com/olekklimek97/gem-scanner ↗
        </a>
      </div>
    </footer>
  );
}
