'use client';

import { useQuery } from '@tanstack/react-query';
import { authFetch } from '@/lib/api';
import { dateTime } from '@/lib/format';

type Overview = {
  text?: string;
  ready_to_send?: { sales_orders?: number; purchase_orders?: number };
  history?: { at: string; by?: string; what?: string; detail?: string }[];
};

/** At the top of the Sage tab: what is ready to go to Sage, and what went out and when. Nothing shows until the server has it. */
export function SageOverview() {
  const o = useQuery({
    queryKey: ['sage', 'overview'],
    queryFn: async (): Promise<Overview | null> => {
      const res = await authFetch('/api/v1/sage/export/overview', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Overview) : null;
    },
    retry: false,
    staleTime: 60_000,
  }).data;
  if (!o) return null;
  const ready = o.ready_to_send;
  return (
    <section className="grid gap-2 rounded-lg border bg-card px-4 py-3" aria-label="Sage at a glance">
      <p className="text-sm font-medium">
        {o.text ?? `Ready to send: ${ready?.sales_orders ?? 0} sales orders, ${ready?.purchase_orders ?? 0} purchase orders.`}
      </p>
      <p className="text-xs text-muted-foreground">
        A Sage file is the import file you download here and load into Sage yourself. Nothing is written into Sage from this app.
      </p>
      {(o.history ?? []).length > 0 && (
        <ul className="grid gap-0.5 text-[13px] text-muted-foreground">
          {o.history!.slice(0, 5).map((h, i) => (
            <li key={i}>
              {dateTime(h.at)}: {h.what ?? 'File made'}
              {h.by ? `, by ${h.by}` : ''}
              {h.detail ? `. ${h.detail}` : ''}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
