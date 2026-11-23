'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { FilterChips, NativeSelect } from '@/components/data/filters';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { date, money } from '@/lib/format';
import { STAGES, leads, scoreTone, type Draft, type Lead } from './api';
import { useLeadList, useLeadSettings, useSuggestions, useSummary } from './use-leads';

/** Leads by stage, best score first, each with the reason it is on the list. Outreach is only ever a draft in Approvals. */
export function Pipeline() {
  const client = useQueryClient();
  const { hasRole } = useAuth();
  const [stage, setStage] = useState('');
  const [due, setDue] = useState(false);
  const summary = useSummary();
  const list = useLeadList(stage, due);
  const suggestions = useSuggestions();
  const settings = useLeadSettings();
  const refresh = () => void client.invalidateQueries({ queryKey: ['leads'] });

  const makeLead = useMutation({
    mutationFn: (id: string) => leads(`/suggestions/${encodeURIComponent(id)}/make-lead`, 'POST'),
    onSuccess: () => {
      toast.success('Added to your leads');
      refresh();
    },
    onError: (e) => toast.error('Not added', { description: e.message }),
  });

  if (list.isPending) return <RowsSkeleton rows={6} />;
  if (!list.data)
    return (
      <EmptyState title="Leads aren’t ready yet">
        The app’s server needs an update to keep leads. Ask whoever runs the server to restart it, then reload this page.
      </EmptyState>
    );

  const by = summary.data?.by_stage ?? {};
  const open = (suggestions.data ?? []).filter((s) => !s.already_a_lead);

  return (
    <div className="grid min-w-0 gap-3">
      {summary.data && list.data.items.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {summary.data.replied_week} replied this week · {summary.data.won} won · {summary.data.lost} lost
          {summary.data.pipeline_value ? ` · pipeline ${money(summary.data.pipeline_value)}` : ''}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <FilterChips
          label="Stage"
          value={stage}
          onChange={setStage}
          options={[{ value: '', label: 'All' }, ...STAGES.map((s) => ({ value: s as string, label: s, count: by[s] }))]}
        />
        <button
          type="button"
          aria-pressed={due}
          onClick={() => setDue((v) => !v)}
          className={`rounded-full border px-2.5 py-1 text-xs ${due ? 'border-foreground bg-foreground text-background' : 'text-muted-foreground'}`}
        >
          Due today ({list.data.due_today})
        </button>
      </div>

      {open.length > 0 && (
        <details className="rounded-lg border bg-card p-3">
          <summary className="cursor-pointer text-[13px] font-medium">From your own history ({open.length})</summary>
          <ul className="mt-2 grid gap-2">
            {open.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
                <span className="min-w-0 text-pretty">{s.sentence}</span>
                {hasRole('OPERATOR') && (
                  <Button size="xs" variant="outline" disabled={makeLead.isPending} onClick={() => makeLead.mutate(s.id)}>
                    Add to leads
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      {list.data.items.length === 0 ? (
        <EmptyState title={due ? 'Nothing is due today' : stage ? 'No leads at this stage' : 'Not started yet'}>
          Add one by hand or from a file on the Find tab, or pick one from your own history above.
        </EmptyState>
      ) : (
        <ul className="overflow-hidden rounded-lg border bg-card" aria-label="Leads">
          {list.data.items.map((l) => (
            <LeadRow key={l.id} lead={l} templates={settings.data?.templates} canAct={hasRole('OPERATOR')} onChanged={refresh} />
          ))}
        </ul>
      )}
    </div>
  );
}

function LeadRow({
  lead: l,
  templates,
  canAct,
  onChanged,
}: {
  lead: Lead;
  templates?: Record<string, { title: string }>;
  canAct: boolean;
  onChanged: () => void;
}) {
  const [template, setTemplate] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const blocked = l.do_not_contact || l.bad_address;
  const why = l.do_not_contact
    ? 'On the do-not-contact list'
    : l.bad_address
      ? 'This address bounced'
      : !l.email
        ? 'No email address yet'
        : '';

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => leads(`/${l.id}`, 'PATCH', body),
    onSuccess: onChanged,
    onError: (e) => toast.error('Not changed', { description: e.message }),
  });
  const outreach = useMutation({
    mutationFn: () => leads<Draft>(`/${l.id}/outreach`, 'POST', template ? { template } : {}),
    onSuccess: (d) => {
      setDraft(d);
      toast.success('Draft waiting for your OK', { description: 'Nothing was sent. Open it in Approvals to check and send it.' });
      onChanged();
    },
    onError: (e) => toast.error('No draft was made', { description: e.message }),
  });
  const sequence = useMutation({
    mutationFn: () => leads(`/${l.id}/sequence`, 'POST'),
    onSuccess: () => {
      toast.success('Follow-ups started', { description: 'Each follow-up is a draft for you to approve.' });
      onChanged();
    },
    onError: (e) => toast.error('Follow-ups not started', { description: e.message }),
  });

  return (
    <li className="grid gap-1.5 border-b p-3 last:border-b-0 md:grid-cols-[1fr_auto] md:items-start">
      <div className="grid min-w-0 gap-0.5">
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <span className="font-medium">{l.name || l.company}</span>
          {l.name && l.company && <span className="text-muted-foreground">{l.company}</span>}
          {l.title && <span className="text-xs text-muted-foreground">{l.title}</span>}
          {l.score != null && <ToneBadge tone={scoreTone(l.score)}>Score {l.score}</ToneBadge>}
          {l.do_not_contact && <ToneBadge tone="bad">Do not contact</ToneBadge>}
          {l.bad_address && <ToneBadge tone="bad">Address bounced</ToneBadge>}
          {l.sequence?.name && (
            <ToneBadge>{l.sequence.paused ? 'Follow-ups paused' : `Follow-up step ${(l.sequence.step ?? 0) + 1}`}</ToneBadge>
          )}
        </div>
        {l.reason && <p className="text-xs text-pretty text-muted-foreground">{l.reason}</p>}
        <p className="text-xs text-muted-foreground">
          {l.email || 'No email yet'}
          {l.next_touch_at ? ` · next touch ${date(l.next_touch_at)}` : ''}
          {l.last_touch_at ? ` · last ${date(l.last_touch_at)}` : ''}
        </p>
        {draft && <p className="text-xs text-ok">Draft “{draft.subject}” is waiting in Approvals.</p>}
      </div>
      {canAct && (
        <div className="flex flex-wrap items-center gap-1.5 md:justify-end">
          <NativeSelect
            aria-label={`Stage of ${l.name || l.company}`}
            value={l.status}
            onChange={(e) => patch.mutate({ status: e.target.value })}
            className="h-7 w-auto text-xs"
          >
            {STAGES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </NativeSelect>
          {templates && Object.keys(templates).length > 0 && (
            <NativeSelect
              aria-label="Which email"
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
              className="h-7 w-auto text-xs"
            >
              <option value="">Usual email</option>
              {Object.entries(templates).map(([k, t]) => (
                <option key={k} value={k}>
                  {t.title}
                </option>
              ))}
            </NativeSelect>
          )}
          <Button size="sm" disabled={Boolean(blocked || !l.email) || outreach.isPending} onClick={() => outreach.mutate()}>
            {outreach.isPending && <Loader2 className="animate-spin" />} Write an email
          </Button>
          {!l.sequence?.name && !blocked && l.email && (
            <Button size="sm" variant="outline" disabled={sequence.isPending} onClick={() => sequence.mutate()}>
              Start follow-ups
            </Button>
          )}
          {why && <span className="basis-full text-xs text-muted-foreground md:text-right">{why}</span>}
        </div>
      )}
    </li>
  );
}
