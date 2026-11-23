'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { EmptyState } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { dateTime, money, words } from '@/lib/format';
import { Card, getJson } from './more-tools';

type Bill = {
  reconciliation_id: string;
  invoice_number: string;
  vendor_name: string;
  po_number: string;
  status_word?: string;
  reconciliation_status?: string;
  authorized_po_total?: number | string;
  actual_billed_total?: number | string;
  cost_variance_amount?: number | string;
  discrepancies?: string[];
  suggested_action?: string;
  reconciled_at?: string;
};

/** The vendor bills the app has checked against their purchase orders, newest first. Read only. */
export function BillHistoryTool() {
  const { hasRole } = useAuth();
  const q = useQuery({
    queryKey: ['reconciliation', 'history'],
    queryFn: () => getJson<Bill[]>('/api/v1/reconciliation/history?limit=50'),
    enabled: hasRole('OPERATOR'),
    retry: false,
  });
  if (!hasRole('OPERATOR'))
    return <EmptyState title="Operators and above">Bill checks are open to operators, managers and admins.</EmptyState>;
  return (
    <Card title="Bills checked" note="Each vendor bill the app compared with its purchase order, and how it came out. Only reports.">
      {q.isPending && <Loader2 className="animate-spin text-muted-foreground" />}
      {q.error && <p className="text-sm text-muted-foreground">This isn’t switched on yet. Tell your administrator.</p>}
      {q.data &&
        (q.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No bills have been checked yet.</p>
        ) : (
          <ul className="grid divide-y rounded-md border text-[13px]">
            {q.data.map((b) => (
              <li key={b.reconciliation_id} className="grid gap-0.5 px-3 py-2">
                <span className="flex flex-wrap items-center gap-x-2 font-medium">
                  {b.vendor_name} · invoice {b.invoice_number} · PO {b.po_number}
                  <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[11px] font-normal text-muted-foreground">
                    {b.status_word ?? words(b.reconciliation_status)}
                  </span>
                </span>
                <span className="text-xs text-muted-foreground">
                  PO {money(b.authorized_po_total)} · billed {money(b.actual_billed_total)} · difference {money(b.cost_variance_amount)}
                  {b.reconciled_at ? ` · ${dateTime(b.reconciled_at)}` : ''}
                </span>
                {(b.discrepancies ?? []).map((d, i) => (
                  <span key={i} className="text-xs text-warn">
                    {d}
                  </span>
                ))}
                {b.suggested_action && <span className="text-xs text-muted-foreground">{b.suggested_action}</span>}
              </li>
            ))}
          </ul>
        ))}
    </Card>
  );
}

type Score = {
  days: number;
  automations: {
    name: string;
    mode?: string;
    drafted: number;
    sent: number;
    cancelled: number;
    lapsed: number;
    failed: number;
    waiting: number;
    send_rate?: number | null;
    advice?: string;
  }[];
  alerts: {
    kind: string;
    raised: number;
    handled: number;
    cleared: number;
    ignored: number;
    open: number;
    handled_rate?: number | null;
    advice?: string;
  }[];
};
const rate = (n?: number | null) => (n == null ? '–' : `${Math.round(n <= 1 ? n * 100 : n)}%`);

/** How the automations have done: what each drafted and what a person did with it, and how alerts were handled. Rules only. */
/** Plain names for the automations the server reports by code. A name not listed is shown as words, never as a raw code. */
const AUTOMATION_NAME: Record<string, string> = {
  rfq_vendor_chaser: 'Chasing vendors for an RFQ price',
  vendor_late_chase: 'Chasing a late vendor',
  vendor_watchdog: 'Watching vendor ship dates',
  vendor_chaser: 'Chasing vendors',
  rfq_board_row: 'Adding a new RFQ to the board',
  rfq_acknowledge: 'Confirming a customer’s RFQ was received',
};
const automationName = (n: string) => {
  const recipe = /^recipe_(\d+)$/.exec(n);
  return AUTOMATION_NAME[n] ?? (recipe ? `Your own rule ${recipe[1]}` : words(n));
};
const MODE_WORD: Record<string, string> = {
  Suggest: 'asks you first',
  Auto: 'runs by itself',
  Off: 'off',
  'Per recipe': 'as set on its board',
};

export function ScorecardTool() {
  const { hasRole } = useAuth();
  const q = useQuery({
    queryKey: ['autopilot', 'scorecard'],
    queryFn: () => getJson<Score>('/api/v1/autopilot/scorecard?days=90'),
    enabled: hasRole('MANAGER'),
    retry: false,
  });
  if (!hasRole('MANAGER')) return <EmptyState title="Managers only">The automation scorecard is open to managers and admins.</EmptyState>;
  return (
    <Card title="Automation scorecard" note="Over the last 90 days: what each automation drafted, and what a person did with it.">
      {q.isPending && <Loader2 className="animate-spin text-muted-foreground" />}
      {q.error && <p className="text-sm text-muted-foreground">This isn’t switched on yet. Tell your administrator.</p>}
      {q.data && (
        <div className="grid gap-4 text-[13px]">
          <ul className="grid divide-y rounded-md border">
            {q.data.automations.map((a) => (
              <li key={a.name} className="grid gap-0.5 px-3 py-2">
                <span className="font-medium">
                  {automationName(a.name)}{' '}
                  <span className="font-normal text-muted-foreground">({MODE_WORD[words(a.mode)] ?? words(a.mode).toLowerCase()})</span>
                </span>
                <span className="text-xs text-muted-foreground">
                  Drafted {a.drafted} · sent {a.sent} ({rate(a.send_rate)}) · cancelled {a.cancelled} · waiting {a.waiting}
                  {a.failed > 0 ? ` · failed ${a.failed}` : ''}
                </span>
                {a.advice && <span className="text-xs">{a.advice}</span>}
              </li>
            ))}
          </ul>
          {q.data.alerts.length > 0 && (
            <ul className="grid divide-y rounded-md border">
              {q.data.alerts.map((a) => (
                <li key={a.kind} className="grid gap-0.5 px-3 py-2">
                  <span className="font-medium">{words(a.kind)}</span>
                  <span className="text-xs text-muted-foreground">
                    Raised {a.raised} · handled {a.handled} ({rate(a.handled_rate)}) · ignored {a.ignored} · open {a.open}
                  </span>
                  {a.advice && <span className="text-xs">{a.advice}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

type Prefs = {
  groups: Record<string, string[]>;
  quiet_start?: string | null;
  quiet_end?: string | null;
  delivery?: 'instant' | 'digest';
  digest_time?: string | null;
};
const GROUP_WORD: Record<string, string> = {
  approvals: 'Things waiting for my OK',
  drafts: 'Drafts',
  alerts: 'Problems and alerts',
  ball: 'Things I am waiting on',
  mentions: 'When someone mentions me',
  mail: 'Mail that needs me',
  follows: 'Things I follow',
  leads: 'Sales leads',
};
const CHANNELS = [
  ['app', 'In the app'],
  ['email', 'Email'],
  ['push', 'Phone'],
  ['sms', 'Text'],
] as const;

/** Each person's own choices: which kinds of news reach them how, quiet hours, and instant or one daily summary. */
export function MyNotificationsTool() {
  const client = useQueryClient();
  const q = useQuery({ queryKey: ['me', 'notifications'], queryFn: () => getJson<Prefs>('/api/v1/me/notifications'), retry: false });
  const [draft, setDraft] = useState<Prefs | null>(null);
  const prefs = draft ?? q.data ?? null;
  const save = useMutation({
    mutationFn: async (p: Prefs) => {
      const res = await authFetch('/api/v1/me/notifications', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(p),
      });
      const body = (await res.json().catch(() => null)) as { detail?: string } | null;
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t save (${res.status}).`);
    },
    onSuccess: () => {
      toast.success('Saved');
      setDraft(null);
      void client.invalidateQueries({ queryKey: ['me', 'notifications'] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });
  if (q.isPending) return <Loader2 className="animate-spin text-muted-foreground" />;
  if (!prefs) return <EmptyState title="Not switched on yet">This isn’t switched on yet. Tell your administrator.</EmptyState>;
  const set = (patch: Partial<Prefs>) => setDraft({ ...prefs, ...patch });
  const toggle = (group: string, ch: string) => {
    const now = prefs.groups[group] ?? [];
    set({ groups: { ...prefs.groups, [group]: now.includes(ch) ? now.filter((c) => c !== ch) : [...now, ch] } });
  };
  return (
    <Card
      title="My notifications"
      note="Choose how each kind of news reaches you. These are saved now. Phone and text messages wait out quiet hours unless they are urgent."
    >
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <th className="px-3 py-1.5 font-medium">Kind of news</th>
              {CHANNELS.map(([c, l]) => (
                <th key={c} className="px-3 py-1.5 font-medium">
                  {l}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.keys(GROUP_WORD).map((g) => (
              <tr key={g} className="border-b last:border-0">
                <td className="px-3 py-1.5">{GROUP_WORD[g]}</td>
                {CHANNELS.map(([c, l]) => (
                  <td key={c} className="px-3 py-1.5">
                    <input
                      type="checkbox"
                      aria-label={`${GROUP_WORD[g]}: ${l}`}
                      className="size-4"
                      checked={(prefs.groups[g] ?? []).includes(c)}
                      onChange={() => toggle(g, c)}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Quiet from">
          <Input
            type="time"
            className="w-32"
            value={prefs.quiet_start ?? ''}
            onChange={(e) => set({ quiet_start: e.target.value || null })}
          />
        </Field>
        <Field label="Quiet until">
          <Input type="time" className="w-32" value={prefs.quiet_end ?? ''} onChange={(e) => set({ quiet_end: e.target.value || null })} />
        </Field>
        <Field label="Send">
          <NativeSelect
            value={prefs.delivery ?? 'instant'}
            onChange={(e) => set({ delivery: e.target.value as 'instant' | 'digest' })}
            className="w-44"
          >
            <option value="instant">As it happens</option>
            <option value="digest">One daily summary</option>
          </NativeSelect>
        </Field>
        {prefs.delivery === 'digest' && (
          <Field label="Summary at">
            <Input
              type="time"
              className="w-32"
              value={prefs.digest_time ?? ''}
              onChange={(e) => set({ digest_time: e.target.value || null })}
            />
          </Field>
        )}
      </div>
      <Button size="sm" className="justify-self-start" disabled={!draft || save.isPending} onClick={() => draft && save.mutate(draft)}>
        {save.isPending && <Loader2 className="animate-spin" />} Save
      </Button>
    </Card>
  );
}
