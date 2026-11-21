'use client';

import { useState } from 'react';
import { useQueryState } from 'nuqs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { FilterChips } from '@/components/data/filters';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { ago, words } from '@/lib/format';
import { DraftsList } from './drafts-list';
import { useInbox } from '@/components/inbox/use-inbox';
import { NewDraft } from './new-draft';
import { ReplyDialog, type Draft } from './reply-dialog';
import { useAiHealth, useMailSettings } from './use-mail-ai';

type Mail = {
  id: number;
  sender: string;
  sender_name?: string | null;
  recipients?: string | null;
  subject: string;
  received_at?: string | null;
  category: string;
  tags?: string[];
  summary?: string | null;
  outcome?: string | null;
  needs_action: boolean;
  handled_at?: string | null;
};
type Feed = { mail: Mail[]; waiting: number; counts?: Record<string, number> };

type Show = 'needs' | 'drafts' | 'all';

async function detail(res: Response, fallback: string) {
  const j = await res.json().catch(() => null);
  return typeof j?.detail === 'string' ? j.detail : `${fallback} (${res.status}).`;
}

/**
 * The shared mailbox as a work queue for the secretary: what needs a reply, what the app already did with it,
 * and a button to write the reply. A reply is only a draft in Approvals; nothing is sent from here.
 */
export function MailQueue() {
  const client = useQueryClient();
  const { employeeEmail, hasRole } = useAuth();
  const [showState, setShowState] = useState<Show>('needs');
  // The Drafts pill is kept in the address (?filter=drafts), so the drafts list below opens on the approvals.
  const [filter, setFilter] = useQueryState('filter');
  const show: Show = filter === 'drafts' ? 'drafts' : showState === 'drafts' ? 'needs' : showState;
  const setShow = (v: Show) => {
    setShowState(v);
    void setFilter(v === 'drafts' ? 'drafts' : null);
  };
  const [composing, setComposing] = useState(false);
  // Drafts waiting for a person's OK, counted from the same queries the Today list uses.
  const draftsWaiting = useInbox().items.filter((i) => i.source === 'drafts').length;
  const [mine, setMine] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const settings = useMailSettings();
  const ai = useAiHealth();
  // The AI is asked only when this person switched it on and it is ready; otherwise the server writes a rule or plain template.
  const useAi = Boolean(settings.data?.ai_opt_in && ai.data?.ready);
  const me = (employeeEmail || '').trim().toLowerCase();

  const q = useQuery({
    queryKey: ['secretary-mail', show, mine],
    queryFn: async (): Promise<Feed> => {
      const params = new URLSearchParams({ limit: '200' });
      if (show === 'needs') params.set('needs_action', 'true');
      if (mine) params.set('mine', 'true');
      const res = await authFetch(`/api/v1/mail?${params}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(await detail(res, 'The mail couldn’t be loaded'));
      return (await res.json()) as Feed;
    },
    refetchInterval: 60_000,
  });

  const reply = useMutation({
    mutationFn: async (id: number): Promise<Draft> => {
      const res = await authFetch(`/api/v1/mail/${id}/draft-reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ use_ai: useAi }),
      });
      if (res.status === 404) throw new Error('Writing replies isn’t switched on yet. Ask whoever runs the server to update it.');
      if (!res.ok) throw new Error(await detail(res, 'The reply couldn’t be written'));
      return (await res.json()) as Draft;
    },
    onSuccess: (d) => setDraft(d),
    onError: (e) => toast.error('No reply was written', { description: e.message }),
  });

  const handled = useMutation({
    mutationFn: async (id: number) => {
      const res = await authFetch(`/api/v1/mail/${id}/handled`, { method: 'POST' });
      if (!res.ok) throw new Error(await detail(res, 'That didn’t go through'));
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['secretary-mail'] }),
    onError: (e) => toast.error('Not marked as handled', { description: e.message }),
  });

  if (show !== 'drafts' && q.isPending) return <RowsSkeleton rows={6} />;
  if (show !== 'drafts' && q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;

  // Safe even if the server ignores "mine": only mail to, from or copied to this person is shown.
  const rows = (q.data?.mail ?? []).filter((m) => !mine || (me && `${m.sender} ${m.recipients ?? ''}`.toLowerCase().includes(me)));
  const mineOk = Boolean(me);

  return (
    <div className="grid min-w-0 gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {hasRole('OPERATOR') && (
          <Button size="sm" onClick={() => setComposing(true)}>
            + New draft
          </Button>
        )}
        <FilterChips
          label="Which mail"
          value={show}
          onChange={setShow}
          options={[
            { value: 'needs', label: 'Needs a reply', count: q.data?.waiting, tone: 'warn' },
            { value: 'drafts', label: 'Waiting for your OK', count: draftsWaiting, tone: draftsWaiting ? 'warn' : undefined },
            { value: 'all', label: 'All mail' },
          ]}
        />
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {show !== 'drafts' && (
            <button
              type="button"
              aria-pressed={mine}
              title="Only mail to, from or copied to your address"
              disabled={!mineOk}
              onClick={() => setMine((v) => !v)}
              className={`rounded-full border px-2.5 py-1 text-xs ${mine ? 'border-foreground bg-foreground text-background' : 'text-muted-foreground'} disabled:opacity-50`}
            >
              Only mine
            </button>
          )}
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Mail that reaches the shared company inbox. Replies are drafts; nothing is sent until you approve it.{' '}
        <a href="#reply-helper" className="underline underline-offset-2">
          Reply helper settings
        </a>
      </p>
      {show === 'drafts' && <DraftsQueue />}
      {show !== 'drafts' && !mineOk && (
        <p className="text-xs text-muted-foreground">Sign in with your work email to see only your own mail.</p>
      )}

      {show === 'drafts' ? null : rows.length === 0 ? (
        <EmptyState title={show === 'needs' ? 'Nothing needs a reply' : 'No mail yet'}>
          {show === 'needs' ? 'Check All mail, or press New draft to write one. ' : ''}
          {mine
            ? 'This shows mail that reaches the shared mailbox with your address on it (to, from or copied).'
            : 'New mail from vendors and customers shows up here once the mailbox is read.'}
        </EmptyState>
      ) : (
        <ul className="overflow-hidden rounded-lg border bg-card" aria-label="Mail">
          {rows.map((m) => {
            const done = Boolean(m.handled_at);
            return (
              <li key={m.id} className="grid gap-1.5 border-b p-3 last:border-b-0 sm:grid-cols-[1fr_auto] sm:items-start">
                <div className="grid min-w-0 gap-0.5">
                  <div className="flex flex-wrap items-center gap-2 text-[13px]">
                    <span className="font-medium">{m.sender_name || m.sender}</span>
                    <span className="text-xs text-muted-foreground">{ago(m.received_at)}</span>
                    {m.needs_action && !done && <ToneBadge tone="warn">Needs a reply</ToneBadge>}
                    {done && <ToneBadge tone="ok">Handled</ToneBadge>}
                    <ToneBadge>{words(m.category)}</ToneBadge>
                    {(m.tags ?? []).map((t) => (
                      <ToneBadge key={t}>{words(t)}</ToneBadge>
                    ))}
                  </div>
                  <p className="truncate text-[13px]">{m.subject || '(no subject)'}</p>
                  {m.summary && <p className="line-clamp-2 text-xs text-pretty text-muted-foreground">{m.summary}</p>}
                  {m.outcome && <p className="text-xs text-muted-foreground">What the app did: {m.outcome}</p>}
                </div>
                {hasRole('OPERATOR') && (
                  <div className="flex gap-1.5 sm:justify-end">
                    <Button size="sm" disabled={reply.isPending} onClick={() => reply.mutate(m.id)}>
                      {reply.isPending && reply.variables === m.id && <Loader2 className="animate-spin" />}
                      Draft a reply
                    </Button>
                    {m.needs_action && !done && (
                      <Button size="sm" variant="outline" disabled={handled.isPending} onClick={() => handled.mutate(m.id)}>
                        Done
                      </Button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <ReplyDialog draft={draft} onClose={() => setDraft(null)} />
      {composing && <NewDraft onClose={() => setComposing(false)} />}
    </div>
  );
}

/** Everything waiting for a person's OK, with the same editing and send controls as on Today. */
function DraftsQueue() {
  return <DraftsList />;
}
