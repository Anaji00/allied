'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { EmptyState, RowsSkeleton } from '@/components/data/states';
import { Field, NativeSelect, PanelHeading } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { leads, type Dnc, type LeadSettings } from './api';
import { useLeadSettings } from './use-leads';

const list = (t: string) => t.split(',').map((s) => s.trim()).filter(Boolean);
const nums = (t: string) => list(t).map(Number).filter((n) => Number.isFinite(n));

/** How the lead engine behaves: who it writes to, how often, and what every email says at the bottom. Managers change it. */
export function Settings() {
  const q = useLeadSettings();
  if (q.isPending) return <RowsSkeleton rows={5} />;
  if (!q.data) return <EmptyState title="Settings aren’t ready yet">The app’s server needs an update to keep lead settings.</EmptyState>;
  return (
    <div className="grid min-w-0 gap-4">
      <SettingsForm initial={q.data} />
      <DoNotContact />
    </div>
  );
}

function SettingsForm({ initial }: { initial: LeadSettings }) {
  const client = useQueryClient();
  const { hasRole } = useAuth();
  const canSave = hasRole('MANAGER');
  const [f, setF] = useState(initial);
  const [seq, setSeq] = useState(initial.sequence_days.join(', '));
  const [hours, setHours] = useState(initial.business_hours.join(' to '));
  const [titles, setTitles] = useState(initial.titles.join(', '));
  const [industries, setIndustries] = useState(initial.industries.join(', '));
  const [tpl, setTpl] = useState(initial.templates);

  const save = useMutation({
    mutationFn: () => {
      const [open, close] = hours.split(/\s*(?:to|-|,)\s*/).map(Number);
      return leads('/settings', 'PUT', {
        ...f,
        sequence_days: nums(seq),
        business_hours: [open, close],
        titles: list(titles),
        industries: list(industries),
        templates: tpl,
      });
    },
    onSuccess: () => {
      toast.success('Saved');
      void client.invalidateQueries({ queryKey: ['leads'] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });
  const set = <K extends keyof LeadSettings>(k: K, v: LeadSettings[K]) => setF({ ...f, [k]: v });

  return (
    <section className="grid gap-4 rounded-lg border bg-card p-4" aria-label="Lead settings">
      <PanelHeading>How the lead engine works</PanelHeading>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Follow-ups" hint="Nothing is ever sent. Each follow-up is a draft you approve.">
          <NativeSelect value={f.sequence_mode} onChange={(e) => set('sequence_mode', e.target.value)} disabled={!canSave}>
            <option value="off">Off</option>
            <option value="suggest">Ask me first</option>
            <option value="auto">Make the drafts for me</option>
          </NativeSelect>
        </Field>
        <Field label="Follow up after (days)" hint="If nobody has replied.">
          <Input type="number" min={1} value={f.follow_up_days} onChange={(e) => set('follow_up_days', Number(e.target.value))} disabled={!canSave} />
        </Field>
        <Field label="Follow-up days" hint="For example 0, 4, 10: the first email, then two follow-ups.">
          <Input value={seq} onChange={(e) => setSeq(e.target.value)} disabled={!canSave} />
        </Field>
        <Field label="Drafts a day, at most">
          <Input type="number" min={1} value={f.daily_draft_limit} onChange={(e) => set('daily_draft_limit', Number(e.target.value))} disabled={!canSave} />
        </Field>
        <Field label="Working hours" hint="Drafts are made only then. For example 8 to 17.">
          <Input value={hours} onChange={(e) => setHours(e.target.value)} disabled={!canSave} />
        </Field>
        <Field label="New leads go to" hint="An email address.">
          <Input value={f.default_owner} onChange={(e) => set('default_owner', e.target.value)} disabled={!canSave} />
        </Field>
        <Field label="Job titles to look for" hint="Separated by commas.">
          <Input value={titles} onChange={(e) => setTitles(e.target.value)} disabled={!canSave} />
        </Field>
        <Field label="Industries" hint="Separated by commas.">
          <Input value={industries} onChange={(e) => setIndustries(e.target.value)} disabled={!canSave} />
        </Field>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Signature">
          <Textarea rows={2} value={f.signature} onChange={(e) => set('signature', e.target.value)} disabled={!canSave} />
        </Field>
        <Field label="Phone">
          <Input value={f.phone} onChange={(e) => set('phone', e.target.value)} disabled={!canSave} />
        </Field>
        <Field label="Postal address" hint="Shown at the bottom of every email. The law in the US requires it.">
          <Textarea rows={2} value={f.postal_address} onChange={(e) => set('postal_address', e.target.value)} disabled={!canSave} />
        </Field>
        <Field label="How to opt out" hint="Shown at the bottom of every email. Ask the owner or a lawyer to check the wording.">
          <Textarea rows={2} value={f.optout_line} onChange={(e) => set('optout_line', e.target.value)} disabled={!canSave} />
        </Field>
      </div>

      <div className="grid gap-2">
        <PanelHeading>The emails</PanelHeading>
        {Object.entries(tpl).map(([key, t]) => (
          <details key={key} className="rounded-md border p-3">
            <summary className="cursor-pointer text-[13px] font-medium">{t.title}</summary>
            <div className="mt-2 grid gap-2">
              <Field label="Subject">
                <Input value={t.subject} disabled={!canSave} onChange={(e) => setTpl({ ...tpl, [key]: { ...t, subject: e.target.value } })} />
              </Field>
              <Field label="Text" hint="Words in braces, like {first_name} and {company}, are filled in for each person.">
                <Textarea rows={8} value={t.body} disabled={!canSave} onChange={(e) => setTpl({ ...tpl, [key]: { ...t, body: e.target.value } })} />
              </Field>
            </div>
          </details>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={!canSave || save.isPending} onClick={() => save.mutate()}>
          {save.isPending && <Loader2 className="animate-spin" />} Save
        </Button>
        {!canSave && <span className="text-xs text-muted-foreground">Only a manager can change these.</span>}
        <span className="text-xs text-muted-foreground">Web search is {initial.search_enabled ? 'on' : 'off'}. The owner switches it on the server.</span>
      </div>
    </section>
  );
}

/** People and companies who must never get an email from here. The server refuses any draft for them. */
function DoNotContact() {
  const client = useQueryClient();
  const { hasRole } = useAuth();
  const [value, setValue] = useState('');
  const [reason, setReason] = useState('');
  const q = useQuery({
    queryKey: ['leads', 'dnc'],
    queryFn: async () => {
      try {
        return await leads<Dnc[]>('/do-not-contact');
      } catch {
        return null;
      }
    },
    retry: false,
  });
  const refresh = () => void client.invalidateQueries({ queryKey: ['leads'] });
  const add = useMutation({
    mutationFn: () => leads('/do-not-contact', 'POST', { value: value.trim(), reason: reason.trim() }),
    onSuccess: () => {
      setValue('');
      setReason('');
      refresh();
    },
    onError: (e) => toast.error('Not added', { description: e.message }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => leads(`/do-not-contact/${id}`, 'DELETE'),
    onSuccess: refresh,
    onError: (e) => toast.error('Not removed', { description: e.message }),
  });

  return (
    <section className="grid gap-3 rounded-lg border bg-card p-4" aria-label="Do not contact">
      <PanelHeading>Never email these</PanelHeading>
      {hasRole('OPERATOR') && (
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Address or company" hint="Like ann@acme.example, or @acme.example for the whole company.">
            <Input value={value} onChange={(e) => setValue(e.target.value)} />
          </Field>
          <Field label="Why (optional)">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          <Button size="sm" disabled={add.isPending || !value.trim()} onClick={() => add.mutate()}>
            Add
          </Button>
        </div>
      )}
      {q.isPending ? null : !q.data ? (
        <p className="text-[13px] text-muted-foreground">Not ready yet.</p>
      ) : q.data.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">Nobody is on the list. People who write “unsubscribe” are added by themselves.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {q.data.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 p-2 text-[13px]">
              <span>
                {d.value} {d.reason && <span className="text-xs text-muted-foreground">· {d.reason}</span>}
              </span>
              {hasRole('MANAGER') && (
                <Button size="xs" variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate(d.id)}>
                  Remove
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
