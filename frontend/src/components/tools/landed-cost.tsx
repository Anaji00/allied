'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, Plus, X } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { EmptyState } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { localIso, money } from '@/lib/format';

type Line = {
  description: string;
  quantity: string;
  unit_cost: string;
  category: string;
  hs_code: string;
  country: string;
  from_ocr: boolean;
};
type Result = {
  id?: number;
  goods?: string;
  tariff_total?: string | null;
  tariff_unknown?: boolean;
  freight?: string | null;
  freight_unknown?: boolean;
  landed_total?: string;
  landed_note?: string;
  tiers?: { name: string; markup_percent: string; price: string; margin: string; firm?: boolean }[];
  lines?: { description?: string; duty?: string | null; duty_note?: string | null; check?: string | null }[];
};
const BLANK: Line = { description: '', quantity: '1', unit_cost: '', category: '', hs_code: '', country: 'CN', from_ocr: false };

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await authFetch(`/api/v1/landed-cost/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const out = (await res.json().catch(() => null)) as (T & { detail?: string }) | null;
  if (!res.ok || !out)
    throw new Error(
      typeof (out as { detail?: string } | null)?.detail === 'string'
        ? (out as { detail: string }).detail
        : `That didn’t work (${res.status}).`,
    );
  return out;
}

/**
 * What a vendor quote really costs landed (goods, duty and freight) and the prices at each markup. A duty or freight that is not known is
 * said to be unknown, never counted as zero. Figures read from a picture are marked to check against the image.
 */
export function LandedCostTool() {
  const { hasRole } = useAuth();
  const [lines, setLines] = useState<Line[]>([{ ...BLANK }]);
  const [freight, setFreight] = useState('');
  const [tiers, setTiers] = useState('20, 30, 40');
  const [res, setRes] = useState<Result | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const body = () => ({
    lines: lines.map((l) => ({ ...l, quantity: l.quantity || '1', hs_code: l.hs_code || undefined, country: l.country || undefined })),
    ...(freight.trim() ? { freight: freight.trim() } : {}),
    currency: 'USD',
    as_of: localIso(new Date()),
    tiers: tiers
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean),
  });
  const calc = useMutation({
    mutationFn: () => post<Result>('calculate', body()),
    onSuccess: (r) => {
      setRes(r);
      setPicked(null);
    },
    onError: (e) => toast.error('It could not be worked out', { description: e.message }),
  });
  const save = useMutation({
    mutationFn: () => post<Result>('sheets', body()),
    onSuccess: (r) => {
      setRes(r);
      toast.success('Sheet saved');
    },
    onError: (e) => toast.error('The sheet was not saved', { description: e.message }),
  });
  const pick = useMutation({
    mutationFn: (tier: string) => post<unknown>(`sheets/${res?.id}/pick`, { tier }),
    onSuccess: (_o, tier) => {
      setPicked(tier);
      toast.success('Price recorded');
    },
    onError: (e) => toast.error('That was not recorded', { description: e.message }),
  });
  const download = async () => {
    const r = await authFetch(`/api/v1/landed-cost/sheets/${res?.id}/xlsx`);
    if (!r.ok) return toast.error('The workbook could not be made');
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = 'landed-cost.xlsx';
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!hasRole('MANAGER'))
    return <EmptyState title="Managers only">The landed cost sheet shows costs and margins, so it is for managers and admins.</EmptyState>;
  const set = (i: number, patch: Partial<Line>) => setLines(lines.map((l, n) => (n === i ? { ...l, ...patch } : l)));
  const ready = lines.every((l) => l.description.trim() && l.unit_cost.trim());

  return (
    <section className="grid max-w-4xl gap-3 rounded-lg border bg-card p-4">
      <div className="grid gap-0.5">
        <h2 className="text-sm font-semibold">Landed cost and markup</h2>
        <p className="text-xs text-muted-foreground">
          Enter the vendor quote’s lines. Leave freight blank if you do not know it yet: it is shown as unknown, not zero.
        </p>
      </div>
      {lines.map((l, i) => (
        <div key={i} className="flex flex-wrap items-end gap-2">
          <Field label={i === 0 ? 'Item' : ''}>
            <Input value={l.description} onChange={(e) => set(i, { description: e.target.value })} className="w-56" />
          </Field>
          <Field label={i === 0 ? 'Quantity' : ''}>
            <Input value={l.quantity} onChange={(e) => set(i, { quantity: e.target.value })} inputMode="decimal" className="w-20" />
          </Field>
          <Field label={i === 0 ? 'Cost each' : ''}>
            <Input value={l.unit_cost} onChange={(e) => set(i, { unit_cost: e.target.value })} inputMode="decimal" className="w-28" />
          </Field>
          <Field label={i === 0 ? 'Kind of goods (for duty)' : ''}>
            <Input value={l.category} onChange={(e) => set(i, { category: e.target.value })} className="w-40" />
          </Field>
          <label className="mb-2 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <input type="checkbox" className="size-3.5" checked={l.from_ocr} onChange={(e) => set(i, { from_ocr: e.target.checked })} />
            Read from a picture
          </label>
          {lines.length > 1 && (
            <button
              type="button"
              aria-label="Remove this line"
              className="mb-2 text-muted-foreground hover:text-foreground"
              onClick={() => setLines(lines.filter((_, n) => n !== i))}
            >
              <X className="size-4" />
            </button>
          )}
        </div>
      ))}
      <div className="flex flex-wrap items-end gap-3">
        <Button type="button" size="sm" variant="outline" onClick={() => setLines([...lines, { ...BLANK }])}>
          <Plus /> Add a line
        </Button>
        <Field label="Freight (blank = not known)">
          <Input value={freight} onChange={(e) => setFreight(e.target.value)} inputMode="decimal" className="w-32" />
        </Field>
        <Field label="Markups to price at (%)">
          <Input value={tiers} onChange={(e) => setTiers(e.target.value)} className="w-40" />
        </Field>
        <Button size="sm" disabled={!ready || calc.isPending} onClick={() => calc.mutate()}>
          {calc.isPending && <Loader2 className="animate-spin" />} Work it out
        </Button>
      </div>

      {res && (
        <div className="grid gap-2 rounded-md bg-muted/40 p-3 text-[13px]">
          <p>
            Goods {money(res.goods ?? 0)} · duty{' '}
            {res.tariff_unknown ? <b className="text-warn">unknown: a tariff rate is missing</b> : money(res.tariff_total ?? 0)} · freight{' '}
            {res.freight_unknown ? <b className="text-warn">unknown</b> : money(res.freight ?? 0)}
          </p>
          <p className="font-medium">
            Landed cost {money(res.landed_total ?? 0)}
            {(res.tariff_unknown || res.freight_unknown) && (
              <span className="font-normal text-warn"> (at least this much: something above is unknown)</span>
            )}
          </p>
          {res.landed_note && <p className="text-xs text-muted-foreground">{res.landed_note}</p>}
          {(res.lines ?? []).map((l, i) =>
            l.check || l.duty_note ? (
              <p key={i} className="text-xs text-warn">
                {l.description}: {l.check ? `check against the image: ${l.check}. ` : ''}
                {l.duty_note ?? ''}
              </p>
            ) : null,
          )}
          <ul className="grid gap-1">
            {(res.tiers ?? []).map((t) => (
              <li key={t.name} className="flex flex-wrap items-center gap-3">
                <span className="w-24 font-medium">{t.name}</span>
                <span>{t.markup_percent}% markup</span>
                <span className="tabular-nums">price {money(t.price)}</span>
                <span className="text-muted-foreground">margin {t.margin}%</span>
                {t.firm === false && <span className="text-xs text-warn">not firm</span>}
                {res.id != null && (
                  <Button
                    size="xs"
                    variant={picked === t.name ? 'default' : 'outline'}
                    disabled={pick.isPending}
                    onClick={() => pick.mutate(t.name)}
                  >
                    {picked === t.name ? 'Chosen' : 'Choose this price'}
                  </Button>
                )}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            {res.id == null ? (
              <Button size="sm" variant="outline" disabled={save.isPending} onClick={() => save.mutate()}>
                Save this sheet
              </Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => void download()}>
                Download the workbook
              </Button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

type Rate = {
  id: number;
  category: string;
  hs_code?: string | null;
  country?: string | null;
  rate_percent: string | number;
  effective_from?: string | null;
  note?: string | null;
};
type Rates = { rates: Rate[]; stale?: boolean; age_days?: number | null; warning?: string | null };

/** The duty rates the sheet uses, newest first. If they are old, it says so, because a wrong rate makes a wrong landed cost. */
export function TariffRates() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [form, setForm] = useState({ category: '', rate_percent: '', hs_code: '', country: '', note: '' });
  const q = useQuery({
    queryKey: ['landed-cost', 'tariffs'],
    queryFn: async (): Promise<Rates | null> => {
      const res = await authFetch('/api/v1/landed-cost/tariffs', { cache: 'no-store' }).catch(() => null);
      return res?.ok ? ((await res.json()) as Rates) : null;
    },
    enabled: hasRole('MANAGER'),
    retry: false,
  });
  const add = useMutation({
    mutationFn: () =>
      post<unknown>('tariffs', {
        category: form.category.trim(),
        rate_percent: form.rate_percent.trim(),
        hs_code: form.hs_code.trim() || undefined,
        country: form.country.trim() || undefined,
        note: form.note.trim() || undefined,
      }),
    onSuccess: () => {
      setForm({ category: '', rate_percent: '', hs_code: '', country: '', note: '' });
      toast.success('Rate added');
      void client.invalidateQueries({ queryKey: ['landed-cost', 'tariffs'] });
    },
    onError: (e) => toast.error('The rate was not added', { description: e.message }),
  });
  if (!hasRole('MANAGER') || !q.data) return null;
  return (
    <section className="grid max-w-4xl gap-3 rounded-lg border bg-card p-4" aria-label="Duty rates">
      <h2 className="text-sm font-semibold">Duty rates</h2>
      {q.data.warning && <p className="rounded-md bg-warn-soft px-3 py-2 text-[13px] text-warn">{q.data.warning}</p>}
      <ul className="grid divide-y rounded-md border text-[13px]">
        {q.data.rates.length === 0 && (
          <li className="px-3 py-2 text-muted-foreground">No rates yet. Duty shows as unknown until you add one.</li>
        )}
        {q.data.rates.map((r) => (
          <li key={r.id} className="flex flex-wrap gap-x-3 px-3 py-1.5">
            <span className="font-medium">{r.category}</span>
            <span className="tabular-nums">{r.rate_percent}%</span>
            <span className="text-xs text-muted-foreground">
              {[r.hs_code, r.country, r.effective_from ? `from ${r.effective_from}` : '', r.note].filter(Boolean).join(' · ')}
            </span>
          </li>
        ))}
      </ul>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (form.category.trim() && form.rate_percent.trim()) add.mutate();
        }}
      >
        <Field label="Kind of goods">
          <Input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="w-44" />
        </Field>
        <Field label="Duty %">
          <Input
            value={form.rate_percent}
            onChange={(e) => setForm({ ...form, rate_percent: e.target.value })}
            inputMode="decimal"
            className="w-24"
          />
        </Field>
        <Field label="Tariff code (optional)">
          <Input value={form.hs_code} onChange={(e) => setForm({ ...form, hs_code: e.target.value })} className="w-36" />
        </Field>
        <Field label="Made in (optional)">
          <Input value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} className="w-24" />
        </Field>
        <Button type="submit" size="sm" disabled={!form.category.trim() || !form.rate_percent.trim() || add.isPending}>
          Add the rate
        </Button>
      </form>
    </section>
  );
}
