import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';

/** One click to the other places the same thing lives: a plain row of links under a record's buttons. Empty links are left out. */
export function GoTo({ links }: { links: { label: string; href?: string | null }[] }) {
  const shown = links.filter((l): l is { label: string; href: string } => Boolean(l.href));
  if (shown.length === 0) return null;
  return (
    <nav aria-label="Go to" className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="text-muted-foreground">Go to</span>
      {shown.map((l) => (
        <Link key={l.label} href={l.href} className="inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 hover:bg-muted">
          {l.label}
          <ArrowUpRight className="size-3 text-muted-foreground" />
        </Link>
      ))}
    </nav>
  );
}
