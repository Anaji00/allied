'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Download, Loader2 } from 'lucide-react';
import { NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { authFetch } from '@/lib/api';
import { downloadBlob } from './job-types';

type Paperwork = {
  total?: number | null;
  total_net_kg?: number | null;
  currency?: string | null;
  complete?: boolean;
  draft?: boolean;
  missing?: { line: number; field: string; text: string }[];
  notes?: string[];
};

const CURRENCIES: [string, string][] = [
  ['USD', 'US dollars'],
  ['CAD', 'Canadian dollars'],
  ['CNY', 'Chinese yuan'],
  ['EUR', 'Euros'],
  ['GBP', 'British pounds'],
  ['JPY', 'Japanese yen'],
  ['MXN', 'Mexican pesos'],
  ['AUD', 'Australian dollars'],
];

/**
 * The papers customs asks for on goods coming in: a commercial invoice and a packing list, made from the purchase order.
 * The currency is never guessed: pick it, and anything the papers still need is listed so it can be filled in first.
 */
export function CustomsPapers({ poId }: { poId: number | string }) {
  // Left blank, the papers follow the shipment's own route (for example Houston means the United States).
  const [destination, setDestination] = useState<'' | 'US' | 'CA'>('');
  const [currency, setCurrency] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const query = [destination && `destination=${destination}`, currency && `currency=${currency}`].filter(Boolean).join('&');
  const base = `/api/v1/purchase-orders/${encodeURIComponent(String(poId))}`;
  const facts = useQuery({
    queryKey: ['po', poId, 'paperwork', destination, currency],
    queryFn: async (): Promise<Paperwork | null> => {
      const res = await authFetch(`${base}/import-paperwork?${query}`, { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Paperwork) : null;
    },
    retry: false,
  });
  const d = facts.data;
  if (facts.isSuccess && !d) return null;

  async function pdf(kind: 'commercial-invoice' | 'packing-list', label: string) {
    setBusy(kind);
    try {
      await downloadBlob(`${base}/${kind}.pdf?${query}`, `${kind}.pdf`);
    } catch (e) {
      toast.error(`Couldn’t make the ${label}`, { description: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(null);
    }
  }

  return (
    <details className="rounded-md border px-3 py-2 text-xs">
      <summary className="cursor-pointer text-sm text-muted-foreground">
        Customs papers{d?.missing?.length ? ` (${d.missing.length} thing${d.missing.length === 1 ? '' : 's'} missing)` : ''}
      </summary>
      <div className="mt-2 grid gap-2">
        <p className="text-muted-foreground">
          Customs asks for a tariff number (what the goods are), where they were made, their weight, and the delivery terms (who pays for
          shipping and where the risk passes, for example FOB).
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex items-center gap-1.5">
            Going to
            <NativeSelect
              value={destination}
              onChange={(e) => setDestination(e.target.value as '' | 'US' | 'CA')}
              className="h-7 w-auto text-xs"
            >
              <option value="">Where the shipment goes</option>
              <option value="CA">Canada</option>
              <option value="US">United States</option>
            </NativeSelect>
          </label>
          <label className="inline-flex items-center gap-1.5">
            Prices are in
            <NativeSelect value={currency} onChange={(e) => setCurrency(e.target.value)} className="h-7 w-auto text-xs">
              <option value="">Choose…</option>
              {CURRENCIES.map(([code, name]) => (
                <option key={code} value={code}>
                  {name} ({code})
                </option>
              ))}
            </NativeSelect>
          </label>
        </div>
        {facts.isPending ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        ) : (
          d && (
            <>
              <p className="text-muted-foreground">
                {d.total != null ? `Total ${d.total}${d.currency ? ` ${d.currency}` : ''}` : 'No total yet'}
                {d.total_net_kg ? ` · ${d.total_net_kg} kg net` : ' · weight not entered'}
                {d.draft ? ' · still a draft' : ''}
              </p>
              {(d.missing ?? []).length > 0 && (
                <ul className="grid gap-0.5 text-warn">
                  {d.missing!.map((m, i) => (
                    <li key={i}>
                      {m.text
                        .replace(
                          /No Incoterm on the purchase order\./,
                          'No delivery terms (Incoterm, for example FOB) on the purchase order.',
                        )
                        .replace(/no unit of measure/, 'no unit (for example EA)')
                        .replace(/no tariff number/, 'no tariff number (what the goods are)')}
                    </li>
                  ))}
                </ul>
              )}
              {(d.notes ?? []).map((n, i) => (
                <p key={i} className="text-muted-foreground">
                  {n}
                </p>
              ))}
            </>
          )
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            size="xs"
            variant="outline"
            title="The bill customs uses to value the goods"
            disabled={busy !== null}
            onClick={() => void pdf('commercial-invoice', 'commercial invoice')}
          >
            {busy === 'commercial-invoice' ? <Loader2 className="animate-spin" /> : <Download />} Commercial invoice
          </Button>
          <Button
            size="xs"
            variant="outline"
            title="What is in each box, with weights"
            disabled={busy !== null}
            onClick={() => void pdf('packing-list', 'packing list')}
          >
            {busy === 'packing-list' ? <Loader2 className="animate-spin" /> : <Download />} Packing list
          </Button>
        </div>
      </div>
    </details>
  );
}
