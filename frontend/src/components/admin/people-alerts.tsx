'use client';

import { useMemo, useState } from 'react';
import { parseAsStringLiteral, useQueryState } from 'nuqs';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Bell, Loader2, Plus, ScanSearch } from 'lucide-react';
import { DataTable, columnHelper } from '@/components/data/data-table';
import { DetailPanel, Facts } from '@/components/data/detail-panel';
import { Field, FilterChips, NativeSelect, PanelHeading, SectionTabs } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button, buttonVariants } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { api, ok } from '@/lib/api/client';
import { dateTime, since, words } from '@/lib/format';
import { cn } from '@/lib/utils';

const TABS = ['history', 'summary', 'contacts', 'accounts'] as const;
type Tab = (typeof TABS)[number];

export function PeopleAlerts() {
  const { hasRole } = useAuth();
  const [tab, setTab] = useQueryState('tab', parseAsStringLiteral(TABS).withDefault('history'));
  const tabs: [Tab, string][] = [
    ['history', 'Alert history'],
    ['summary', 'Daily summary'],
  ];
  if (hasRole('MANAGER')) tabs.push(['contacts', 'Who gets told']);
  if (hasRole('ADMIN')) tabs.push(['accounts', 'User accounts']);
  const current = tabs.some(([t]) => t === tab) ? tab : 'history';

  return (
    <div className="grid gap-4">
      <SectionTabs label="People and alerts" value={current} onChange={(t) => void setTab(t === 'history' ? null : t)} tabs={tabs} />
      {current === 'history' && <History />}
      {current === 'summary' && <Summary />}
      {current === 'contacts' && <Contacts />}
      {current === 'accounts' && <Accounts />}
    </div>
  );
}

/* --------------------------------------------------------------- history */

type Alert = {
  id: number;
  kind: string;
  severity: string;
  title: string;
  body?: string | null;
  link?: string | null;
  created_at?: string | null;
  acknowledged_at?: string | null;
  recipients?: string[] | null;
  deliveries?: Record<string, unknown> | null;
};
type Channels = {
  email?: { mode?: string; detail?: string };
  push?: { on?: boolean; detail?: string };
  sms?: { on?: boolean; detail?: string };
  team_chat?: { on?: boolean };
  leadership?: string[];
  quiet_hours?: string;
  daily_summary_hour?: number;
};

const SEVERITY: Record<string, { label: string; tone: Tone }> = {
  critical: { label: 'Urgent', tone: 'bad' },
  warning: { label: 'Behind', tone: 'warn' },
  info: { label: 'FYI', tone: 'info' },
};
const CHANNEL: Record<string, string> = { email: 'Email', push: 'Phone push', sms: 'Text', _team_chat: 'Team chat', _push: 'Phone push' };
const CHECKED: Record<string, string> = {
  due_soon: 'due soon',
  overdue: 'overdue',
  escalated: 'passed up to a manager',
  review_waiting: 'waiting for review',
  project_overdue: 'projects behind',
  shipments: 'shipments',
  unplanned: 'not planned',
};

// Only same-site paths: '//host' and '/\host' would leave the site.
const internal = (link?: string | null) => typeof link === 'string' && link.startsWith('/') && !link.startsWith('//') && !link.startsWith('/\\');

function delivery(value: unknown) {
  if (value == null) return '–';
  if (typeof value !== 'object') return String(value);
  return (
    Object.entries(value as Record<string, unknown>)
      .map(([ch, v]) => `${CHANNEL[ch] ?? ch}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
      .join(' · ') || '–'
  );
}

const acol = columnHelper<Alert>();

function History() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const wide = useMediaQuery('(min-width: 1280px)');
  const isManager = hasRole('MANAGER');
  const [show, setShow] = useState<'open' | 'all'>('all');
  const [everyone, setEveryone] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [testing, setTesting] = useState(false);
  const list = useQuery({
    queryKey: ['alerts', 'history', show, everyone],
    queryFn: async () =>
      (await ok(api.GET('/api/v1/alerts', { params: { query: { open_only: show === 'open', everyone: everyone && isManager, limit: 200 } } }))) as { items: Alert[]; open: number },
  });
  const ack = useMutation({
    mutationFn: async (id: number) => ok(api.POST('/api/v1/alerts/{notification_id}/acknowledge', { params: { path: { notification_id: id } } })),
    onSuccess: () => {
      toast.success('Marked handled');
      void client.invalidateQueries({ queryKey: ['alerts'] });
      void client.invalidateQueries({ queryKey: ['inbox'] });
    },
    onError: (e) => toast.error('It wasn’t marked handled', { description: e.message }),
  });
  const check = useMutation({
    mutationFn: async () => (await ok(api.POST('/api/v1/alerts/check-now'))) as Record<string, number>,
    onSuccess: (counts) => {
      const parts = Object.entries(counts).filter(([, n]) => n > 0);
      const total = parts.reduce((s, [, n]) => s + n, 0);
      if (total) toast.success(`Sent ${total} new alert${total === 1 ? '' : 's'}`, { description: parts.map(([k, n]) => `${n} ${CHECKED[k] ?? k}`).join(', ') });
      else toast.success('Schedules checked', { description: 'No new alerts were needed. Nobody is told twice about the same thing.' });
      void client.invalidateQueries({ queryKey: ['alerts'] });
      void client.invalidateQueries({ queryKey: ['inbox'] });
    },
    onError: (e) => toast.error('The checks didn’t run', { description: e.message }),
  });

  const rows = list.data?.items ?? [];
  const selected = rows.find((a) => a.id === openId);

  const columns = acol.columns([
    acol.accessor('created_at', { header: 'Sent', cell: (c) => <span className="whitespace-nowrap">{dateTime(c.getValue())}</span> }),
    acol.accessor('severity', {
      header: 'Kind',
      cell: (c) => {
        const s = SEVERITY[c.getValue()] ?? { label: words(c.getValue()), tone: 'neutral' as Tone };
        return <ToneBadge tone={s.tone}>{s.label}</ToneBadge>;
      },
    }),
    acol.accessor('title', { header: 'Alert', meta: { className: 'max-w-[28rem] truncate' } }),
    acol.accessor('acknowledged_at', {
      header: 'Handled',
      cell: (c) => (c.getValue() ? <span className="text-muted-foreground">{since(c.getValue())}</span> : <ToneBadge tone="warn">Open</ToneBadge>),
    }),
  ]);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChips
          label="Show"
          value={show}
          onChange={(v) => setShow(v)}
          options={[
            { value: 'all', label: 'All' },
            { value: 'open', label: 'Not handled', count: list.data?.open, tone: 'warn' },
          ]}
        />
        {isManager && (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={everyone} onChange={(e) => setEveryone(e.target.checked)} />
            Everyone’s alerts
          </label>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => setTesting(true)}>
            <Bell /> Send me a test alert
          </Button>
          {hasRole('OPERATOR') && (
            <Button size="sm" variant="outline" disabled={check.isPending} onClick={() => check.mutate()}>
              {check.isPending ? <Loader2 className="animate-spin" /> : <ScanSearch />}
              Check schedules now
            </Button>
          )}
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Open alerts are also in the <Link className="underline underline-offset-2" href="/">Inbox</Link>, where you can work through them with the keyboard.
      </p>

      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className="min-w-0">
          {list.isPending ? (
            <RowsSkeleton rows={6} />
          ) : list.error ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : (
            <DataTable
              label="Alerts"
              columns={columns}
              data={rows}
              getRowId={(a) => String(a.id)}
              selectedId={openId != null ? String(openId) : null}
              onSelect={(a) => setOpenId(openId === a.id ? null : a.id)}
              minWidth={640}
              empty={<EmptyState title={show === 'open' ? 'Nothing open' : 'No alerts yet'} />}
            />
          )}
        </div>
        <DetailPanel open={Boolean(selected)} onClose={() => setOpenId(null)} title={selected?.title ?? ''} description={selected ? dateTime(selected.created_at) : undefined}>
          {selected && (
            <div className="grid gap-3 text-[13px]">
              {selected.body && <p className="whitespace-pre-wrap">{selected.body}</p>}
              <Facts
                items={[
                  ['Kind', words(selected.kind)],
                  ['Handled', selected.acknowledged_at ? dateTime(selected.acknowledged_at) : 'Not yet'],
                  ...(selected.recipients?.length ? ([['To', selected.recipients.join(', ')]] as [string, string][]) : []),
                  ...Object.entries(selected.deliveries ?? {}).map(([who, v]) => [CHANNEL[who] ?? who, delivery(v)] as [string, string]),
                ]}
              />
              <div className="flex flex-wrap gap-2">
                {!selected.acknowledged_at && (
                  <Button size="sm" disabled={ack.isPending} onClick={() => ack.mutate(selected.id)}>
                    Mark handled
                  </Button>
                )}
                {internal(selected.link) && (
                  <Link href={selected.link!} className={buttonVariants({ size: 'sm', variant: 'outline' })}>
                    Open
                  </Link>
                )}
              </div>
            </div>
          )}
        </DetailPanel>
      </div>
      <TestAlert open={testing} onOpenChange={setTesting} />
    </div>
  );
}

function TestAlert({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const client = useQueryClient();
  const send = useMutation({
    mutationFn: async () => (await ok(api.POST('/api/v1/alerts/test', { params: { query: { severity: 'warning' } } }))) as { id: number | null; delivered: Record<string, unknown> | null },
    onSuccess: (r) => {
      onOpenChange(false);
      if (!r.id || !r.delivered) toast.warning('No test alert was sent', { description: 'You may not be set up to get alerts yet. Ask an admin to add you under Who gets told.' });
      else {
        const parts = Object.entries(r.delivered);
        toast.success('Test alert sent', { description: parts.length ? parts.map(([w, v]) => `${CHANNEL[w] ?? w}: ${delivery(v)}`).join(' | ') : 'It’s in your alerts. No email, push or text went out.' });
      }
      void client.invalidateQueries({ queryKey: ['alerts'] });
      void client.invalidateQueries({ queryKey: ['inbox'] });
    },
    onError: (e) => toast.error('The test alert failed', { description: e.message }),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Send yourself a test alert?</DialogTitle>
          <DialogDescription>
            It goes only to you, on every channel you’re set up for: email, phone push or text. Use it to check alerts reach you.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={send.isPending} onClick={() => send.mutate()}>
            {send.isPending && <Loader2 className="animate-spin" />}
            Send it to me
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* --------------------------------------------------------------- summary */

const SECTIONS: [string, string, Tone][] = [
  ['behind', 'Behind schedule', 'bad'],
  ['shipments', 'Shipments needing attention', 'warn'],
  ['review', 'Design documents waiting for review', 'info'],
  ['due_week', 'Due this week', 'neutral'],
];

function Summary() {
  const { hasRole } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const data = useQuery({ queryKey: ['alerts', 'summary'], queryFn: async () => (await ok(api.GET('/api/v1/alerts/summary'))) as Record<string, unknown> & { text?: string; urgent_open?: number } });
  const channels = useQuery({
    queryKey: ['alerts', 'channels-public'],
    queryFn: async () => ((await ok(api.GET('/api/v1/alerts', { params: { query: { open_only: true, limit: 1 } } }))) as { channels?: Channels }).channels ?? {},
    staleTime: 10 * 60_000,
  });
  const send = useMutation({
    mutationFn: async () => (await ok(api.POST('/api/v1/alerts/summary/send'))) as { id?: number | null },
    onSuccess: (r) => {
      setConfirming(false);
      if (r.id) toast.success('Summary sent', { description: 'It’s also in each recipient’s alerts.' });
      else toast.warning('Nothing was sent', { description: 'No one is set to get the summary. Add leadership under Who gets told.' });
    },
    onError: (e) => toast.error('The summary wasn’t sent', { description: e.message }),
  });

  if (data.isPending) return <RowsSkeleton rows={6} />;
  if (data.error) return <ErrorState error={data.error} onRetry={() => void data.refetch()} />;
  const d = data.data!;
  const hour = channels.data?.daily_summary_hour;
  const when = hour != null ? `every weekday at ${String(hour).padStart(2, '0')}:00` : 'every weekday morning';
  const lists = SECTIONS.map(([key, title, tone]) => [title, tone, (d[key] as string[] | undefined) ?? []] as const);
  const empty = lists.every(([, , l]) => !l.length) && !d.urgent_open;

  return (
    <div className="grid max-w-3xl gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted-foreground">What leadership gets by email {when}, as it stands right now.</p>
        {hasRole('MANAGER') && (
          <Button size="sm" className="ml-auto" onClick={() => setConfirming(true)}>
            Send it now…
          </Button>
        )}
      </div>
      {(d.urgent_open ?? 0) > 0 && (
        <p className="rounded-md bg-bad-soft p-3 text-sm text-bad">
          {d.urgent_open} urgent alert{d.urgent_open === 1 ? '' : 's'} not yet handled.
        </p>
      )}
      {empty ? (
        <EmptyState title="Nothing is behind schedule">Nothing needs leadership today.</EmptyState>
      ) : (
        lists
          .filter(([, , l]) => l.length)
          .map(([title, tone, l]) => (
            <section key={title} className="grid gap-2 rounded-lg border bg-card p-4">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold">{title}</h2>
                <ToneBadge tone={tone}>{l.length}</ToneBadge>
              </div>
              <ul className="grid list-disc gap-1 pl-5 text-[13px]">
                {l.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            </section>
          ))
      )}
      {d.text && (
        <details className="text-[13px]">
          <summary className="cursor-pointer text-muted-foreground">See the email text</summary>
          <pre className="mt-2 rounded-md bg-muted/50 p-3 font-mono text-xs whitespace-pre-wrap">{d.text}</pre>
        </details>
      )}
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Send the summary now?</DialogTitle>
            <DialogDescription>It emails everyone set to get the daily summary, as well as the usual morning one.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button disabled={send.isPending} onClick={() => send.mutate()}>
              {send.isPending && <Loader2 className="animate-spin" />}
              Send it
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* -------------------------------------------------------------- contacts */

type Contact = { email: string; name: string; role_tag: string; phone?: string | null; channels: string[]; daily_summary: boolean; active: boolean };
const GROUPS = [
  ['leadership', 'Leadership'],
  ['engineer', 'Engineer'],
  ['operations', 'Operations'],
  ['staff', 'Staff'],
] as const;
const CHANNELS = [
  ['email', 'Email'],
  ['push', 'Phone push'],
  ['sms', 'Text message'],
] as const;
const E164 = /^\+[1-9]\d{6,14}$/;
const EMPTY_CONTACT: Contact = { email: '', name: '', role_tag: 'staff', phone: '', channels: ['email'], daily_summary: false, active: true };

const ccol = columnHelper<Contact>();
const contactColumns = ccol.columns([
  ccol.accessor('name', {
    header: 'Person',
    cell: (c) => (
      <span className="grid">
        <span className="font-medium">{c.getValue() || '–'}</span>
        <span className="text-[11px] text-muted-foreground">{c.row.original.email}</span>
      </span>
    ),
  }),
  ccol.accessor('role_tag', { header: 'Group', cell: (c) => GROUPS.find(([v]) => v === c.getValue())?.[1] ?? c.getValue() }),
  ccol.accessor('channels', { header: 'Told by', cell: (c) => c.getValue().map((x) => CHANNELS.find(([v]) => v === x)?.[1] ?? x).join(', ') || '–' }),
  ccol.accessor('daily_summary', { header: 'Summary', cell: (c) => (c.getValue() ? 'Yes' : 'No') }),
  ccol.accessor('active', { header: 'Active', cell: (c) => <ToneBadge tone={c.getValue() ? 'ok' : 'neutral'}>{c.getValue() ? 'Yes' : 'Paused'}</ToneBadge> }),
]);

function Contacts() {
  const { hasRole } = useAuth();
  const isAdmin = hasRole('ADMIN');
  const [editing, setEditing] = useState<Contact | null>(null);
  const data = useQuery({
    queryKey: ['alerts', 'contacts'],
    queryFn: async () => (await ok(api.GET('/api/v1/alerts/contacts'))) as { contacts: Contact[]; channels: Channels },
  });
  const contacts = useMemo(() => [...(data.data?.contacts ?? [])].sort((a, b) => a.name.localeCompare(b.name)), [data.data]);
  const ch = data.data?.channels;

  if (data.isPending) return <RowsSkeleton rows={5} />;
  if (data.error) return <ErrorState error={data.error} onRetry={() => void data.refetch()} />;

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm text-muted-foreground">Email for every alert; phone push for urgent and late work; texts only when urgent. Push and texts wait out quiet hours unless urgent.</p>
          {isAdmin && (
            <Button size="sm" className="ml-auto" onClick={() => setEditing(EMPTY_CONTACT)}>
              <Plus /> Add a person
            </Button>
          )}
        </div>
        <DataTable
          label="Who gets told"
          columns={contactColumns}
          data={contacts}
          getRowId={(c) => c.email}
          onSelect={isAdmin ? (c) => setEditing({ ...EMPTY_CONTACT, ...c, phone: c.phone ?? '' }) : undefined}
          minWidth={640}
          empty={<EmptyState title="No one is set up yet">Alerts go to each job’s owner by email.</EmptyState>}
        />
        {isAdmin && <p className="text-xs text-muted-foreground">Click a person to change how they’re told.</p>}
      </div>
      {ch && (
        <section className="grid gap-3 rounded-lg border bg-card p-4 text-[13px]">
          <h2 className="text-sm font-semibold">Channels</h2>
          <Facts
            items={[
              ['Email', ch.email?.mode === 'LIVE' ? 'On' : ch.email?.mode === 'REDIRECTED' ? 'Test mode (redirected)' : 'Off, drafts only'],
              ['Phone push', ch.push?.on ? 'On' : 'Off'],
              ['Text messages', ch.sms?.on ? 'On' : 'Off'],
              ['Team chat', ch.team_chat?.on ? 'On' : 'Off'],
              ['Leadership', ch.leadership?.length ? ch.leadership.join(', ') : 'No one yet'],
              ['Quiet hours', ch.quiet_hours ?? '–'],
              ['Daily summary', ch.daily_summary_hour != null ? `${String(ch.daily_summary_hour).padStart(2, '0')}:00 weekdays` : '–'],
            ]}
          />
          {ch.email?.detail && <p className="text-xs text-muted-foreground">{ch.email.detail}</p>}
        </section>
      )}
      {editing && <ContactDialog key={editing.email || 'new'} initial={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function ContactDialog({ initial, onClose }: { initial: Contact; onClose: () => void }) {
  const client = useQueryClient();
  const [form, setForm] = useState(initial);
  const [problem, setProblem] = useState('');
  const existing = Boolean(initial.email);
  const save = useMutation({
    mutationFn: async (body: Contact) => ok(api.PUT('/api/v1/alerts/contacts', { body: body as never })),
    onSuccess: () => {
      toast.success(`Saved ${form.name || form.email}`);
      void client.invalidateQueries({ queryKey: ['alerts'] });
      onClose();
    },
    onError: (e) => setProblem(e.message),
  });

  function submit() {
    const email = form.email.trim();
    const phone = (form.phone ?? '').trim().replace(/[\s()-]/g, '');
    const issue =
      !email.includes('@')
        ? 'Enter an email address.'
        : !form.name.trim()
          ? 'Enter a name.'
          : phone && !E164.test(phone)
            ? 'Write the phone number with + and the country code, like +12055550100.'
            : form.channels.includes('sms') && !phone
              ? 'Text messages need a phone number.'
              : !form.channels.length
                ? 'Pick at least one way to tell them.'
                : '';
    setProblem(issue);
    if (!issue) save.mutate({ ...form, email, name: form.name.trim(), phone: phone || null });
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90svh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{existing ? `How ${initial.name || initial.email} is told` : 'Add a person'}</DialogTitle>
          <DialogDescription>Who gets which alerts, and how. Saving changes only these settings.</DialogDescription>
        </DialogHeader>
        <form
          id="contact"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Email">
              <Input type="email" value={form.email} disabled={existing} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </Field>
            <Field label="Name">
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Group">
              <NativeSelect value={form.role_tag} onChange={(e) => setForm({ ...form, role_tag: e.target.value })}>
                {GROUPS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Mobile phone" hint="With + and country code.">
              <Input type="tel" value={form.phone ?? ''} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+12055550100" />
            </Field>
          </div>
          <fieldset className="grid gap-1.5">
            <legend className="mb-1 text-sm font-medium">Tell them by</legend>
            <div className="flex flex-wrap gap-4 text-sm">
              {CHANNELS.map(([v, l]) => (
                <label key={v} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form.channels.includes(v)}
                    onChange={(e) => setForm({ ...form, channels: e.target.checked ? [...new Set([...form.channels, v])] : form.channels.filter((c) => c !== v) })}
                  />
                  {l}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.daily_summary} onChange={(e) => setForm({ ...form, daily_summary: e.target.checked })} />
            Gets the daily summary
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />
            Active (untick to pause all their alerts)
          </label>
          {problem && (
            <p role="alert" className="text-sm text-bad">
              {problem}
            </p>
          )}
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="contact" disabled={save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* -------------------------------------------------------------- accounts */

type User = { user_id: string; email: string; full_name: string; role: string; facility: string; is_active: boolean; last_login?: string | null };
const ROLES = [
  ['VIEWER', 'Viewer', 'Sees everything, changes nothing'],
  ['OPERATOR', 'Operator', 'Day-to-day work: orders, shipments, reviews'],
  ['MANAGER', 'Manager', 'Plus Sage files, imports, the audit trail and everyone’s alerts'],
  ['ADMIN', 'Admin', 'Plus user accounts and who gets told'],
] as const;
const SITES = ['HQ', 'EDMONTON', 'ZIBO'] as const;
const SITE_LABEL: Record<string, string> = { HQ: 'HQ', EDMONTON: 'Edmonton', ZIBO: 'Zibo' };
const siteName = (s: string) => SITE_LABEL[s] ?? words(s);
const ROLE_TONE: Record<string, Tone> = { ADMIN: 'bad', MANAGER: 'warn', OPERATOR: 'info', VIEWER: 'neutral' };

const ucol = columnHelper<User>();
const userColumns = ucol.columns([
  ucol.accessor('full_name', {
    header: 'Person',
    cell: (c) => (
      <span className="grid">
        <span className="font-medium">{c.getValue()}</span>
        <span className="text-[11px] text-muted-foreground">{c.row.original.email}</span>
      </span>
    ),
  }),
  ucol.accessor('role', { header: 'Access', cell: (c) => <ToneBadge tone={ROLE_TONE[c.getValue()] ?? 'neutral'}>{words(c.getValue())}</ToneBadge> }),
  ucol.accessor('facility', { header: 'Site', cell: (c) => siteName(c.getValue()) }),
  ucol.accessor('last_login', { header: 'Last signed in', cell: (c) => (c.getValue() ? since(c.getValue()) : 'Never') }),
  ucol.accessor('is_active', { header: 'Account', cell: (c) => <ToneBadge tone={c.getValue() ? 'ok' : 'neutral'}>{c.getValue() ? 'Active' : 'Disabled'}</ToneBadge> }),
]);

function Accounts() {
  const wide = useMediaQuery('(min-width: 1280px)');
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const users = useQuery({ queryKey: ['users'], queryFn: async () => (await ok(api.GET('/api/v1/auth/users'))) as User[] });
  const rows = users.data ?? [];
  const selected = rows.find((u) => u.email === open);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted-foreground">Who can sign in, and what they can do. Changes apply to their very next click.</p>
        <Button size="sm" className="ml-auto" onClick={() => setAdding(true)}>
          <Plus /> Add a user
        </Button>
      </div>
      <div className={cn('grid items-start gap-4', wide && selected && 'grid-cols-[minmax(0,1fr)_26rem]')}>
        <div className="min-w-0">
          {users.isPending ? (
            <RowsSkeleton rows={5} />
          ) : users.error ? (
            <ErrorState error={users.error} onRetry={() => void users.refetch()} />
          ) : (
            <DataTable label="User accounts" columns={userColumns} data={rows} getRowId={(u) => u.email} selectedId={open} onSelect={(u) => setOpen(open === u.email ? null : u.email)} minWidth={640} />
          )}
        </div>
        <DetailPanel open={Boolean(selected)} onClose={() => setOpen(null)} title={selected?.full_name ?? ''} description={selected?.email}>
          {selected && <UserForm key={`${selected.email}-${selected.role}-${selected.facility}-${selected.is_active}`} user={selected} />}
        </DetailPanel>
      </div>
      <AddUser open={adding} onOpenChange={setAdding} onAdded={setOpen} />
    </div>
  );
}

function UserForm({ user }: { user: User }) {
  const { employeeEmail } = useAuth();
  const client = useQueryClient();
  const me = (employeeEmail ?? '').toLowerCase() === user.email;
  const [name, setName] = useState(user.full_name);
  const [role, setRole] = useState(user.role);
  const [site, setSite] = useState(user.facility);
  const [password, setPassword] = useState('');
  const [confirmDisable, setConfirmDisable] = useState(false);

  const save = useMutation({
    mutationFn: async (body: { full_name?: string; role?: string; facility?: string; is_active?: boolean; new_password?: string }) =>
      ok(api.PATCH('/api/v1/auth/users/{email}', { params: { path: { email: user.email } }, body })),
    onSuccess: (_r, body) => {
      toast.success(body.is_active === false ? `${user.full_name} can no longer sign in` : body.is_active ? `${user.full_name} can sign in again` : 'Saved');
      setPassword('');
      setConfirmDisable(false);
      void client.invalidateQueries({ queryKey: ['users'] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });
  const changed = name.trim() !== user.full_name || role !== user.role || site !== user.facility;

  return (
    <div className="grid gap-4">
      <Facts items={[['Last signed in', user.last_login ? dateTime(user.last_login) : 'Never'], ['Account', user.is_active ? 'Active' : 'Disabled']]} />
      <form
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (changed) save.mutate({ full_name: name.trim(), role, facility: site });
        }}
      >
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Access" hint={me ? 'You can’t change your own access. Ask another admin.' : ROLES.find(([v]) => v === role)?.[2]}>
          <NativeSelect value={role} disabled={me} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Site">
          <NativeSelect value={site} onChange={(e) => setSite(e.target.value)}>
            {SITES.map((s) => (
              <option key={s} value={s}>
                {siteName(s)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Button type="submit" size="sm" className="justify-self-start" disabled={!changed || save.isPending}>
          {save.isPending && <Loader2 className="animate-spin" />}
          Save changes
        </Button>
      </form>

      <form
        className="grid gap-2 rounded-md border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (password.length >= 8) save.mutate({ new_password: password });
        }}
      >
        <PanelHeading>Set a new password</PanelHeading>
        <Field label="New password" hint="At least 8 characters. Give it to them in person, not by email.">
          <Input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Button type="submit" size="sm" variant="outline" className="justify-self-start" disabled={password.length < 8 || save.isPending}>
          Set password
        </Button>
      </form>

      {!me &&
        (user.is_active ? (
          confirmDisable ? (
            <div className="grid gap-2 rounded-md bg-bad-soft p-3 text-[13px]">
              <p className="text-bad">{user.full_name} will be signed out and can’t sign in until re-enabled. Their history stays.</p>
              <div className="flex gap-2">
                <Button size="sm" variant="destructive" disabled={save.isPending} onClick={() => save.mutate({ is_active: false })}>
                  Disable the account
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirmDisable(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <Button size="sm" variant="ghost" className="justify-self-start text-bad" onClick={() => setConfirmDisable(true)}>
              Disable the account…
            </Button>
          )
        ) : (
          <Button size="sm" variant="outline" className="justify-self-start" disabled={save.isPending} onClick={() => save.mutate({ is_active: true })}>
            Enable the account
          </Button>
        ))}
    </div>
  );
}

function AddUser({ open, onOpenChange, onAdded }: { open: boolean; onOpenChange: (open: boolean) => void; onAdded: (email: string) => void }) {
  const client = useQueryClient();
  const empty = { email: '', full_name: '', role: 'VIEWER', facility: 'HQ', password: '' };
  const [form, setForm] = useState(empty);
  const add = useMutation({
    mutationFn: async () => (await ok(api.POST('/api/v1/auth/users', { body: { ...form, email: form.email.trim(), full_name: form.full_name.trim() } }))) as User,
    onSuccess: (u) => {
      toast.success(`Added ${u.full_name}`, { description: 'Give them their password in person.' });
      void client.invalidateQueries({ queryKey: ['users'] });
      setForm(empty);
      onOpenChange(false);
      onAdded(u.email);
    },
    onError: (e) => toast.error('The user wasn’t added', { description: e.message }),
  });
  const ready = form.email.includes('@') && form.full_name.trim() && form.password.length >= 8;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a user</DialogTitle>
          <DialogDescription>They sign in with this email and password. To get alerts too, add them under Who gets told.</DialogDescription>
        </DialogHeader>
        <form
          id="add-user"
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) add.mutate();
          }}
        >
          <Field label="Name">
            <Input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
          </Field>
          <Field label="Email">
            <Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
          </Field>
          <Field label="Access">
            <NativeSelect value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {ROLES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Site">
            <NativeSelect value={form.facility} onChange={(e) => setForm({ ...form, facility: e.target.value })}>
              {SITES.map((s) => (
                <option key={s} value={s}>
                  {siteName(s)}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Password" hint="At least 8 characters." className="sm:col-span-2">
            <Input type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
          </Field>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="add-user" disabled={!ready || add.isPending}>
            {add.isPending && <Loader2 className="animate-spin" />}
            Add user
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
