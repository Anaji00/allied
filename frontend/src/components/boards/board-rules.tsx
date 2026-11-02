'use client';

import { useState } from 'react';
import { Loader2, Play, Plus } from 'lucide-react';
import { Field, NativeSelect, PanelHeading } from '@/components/data/filters';
import { ToneBadge } from '@/components/data/tone-badge';
import { DictateButton, addDictation } from '@/components/data/voice';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { words } from '@/lib/format';
import { useAddStarter, useRules, useStarters, type Did, type Rule, type RuleIn, type Score, type Starter } from './use-automations';
import type { Board } from './use-boards';

/** "Sent 8 of 10 (80%)" and what to do about it, for managers. */
export function ScoreLine({ score }: { score: Score | undefined }) {
  if (!score || !score.drafted) return null;
  const decided = score.sent + score.cancelled + score.lapsed;
  return (
    <p className="text-xs text-muted-foreground">
      Last 90 days: {score.drafted} drafted, {score.sent} sent
      {decided > 0 && score.send_rate != null && ` (${Math.round(score.send_rate * 100)}% of those decided)`}
      {score.waiting > 0 && `, ${score.waiting} waiting`}. <span className="text-foreground">{score.advice}</span>
    </p>
  );
}

/** People's own "when this, then that" rules for one board. Managers add, switch and remove them. */
export function BoardRules({
  board,
  rules,
  scores,
  onOpenItem,
}: {
  board: Board;
  rules: Rule[];
  scores: Map<string, Score>;
  onOpenItem?: (itemId: string) => void;
}) {
  const { hasRole } = useAuth();
  const manager = hasRole('MANAGER');
  const { change, remove, checkDates } = useRules(board.board_id);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<number | null>(null);
  const hasDates = rules.some((r) => r.trigger !== 'status_is' && r.enabled);
  if (board.role !== 'projects' && board.role !== 'vendors' && board.role !== 'rfqs') return null;

  return (
    <section className="grid gap-2">
      <div className="flex items-center justify-between gap-2">
        <PanelHeading>Your own rules</PanelHeading>
        {manager && !adding && (
          <Button size="xs" variant="outline" onClick={() => setAdding(true)}>
            <Plus /> Add a rule
          </Button>
        )}
      </div>
      {!rules.length && !adding && (
        <p className="text-xs text-muted-foreground">
          None yet.{' '}
          {manager ? 'Add one to tell someone, or draft an email, when a status or date on this board says so.' : 'A manager can add them.'}
        </p>
      )}
      {adding && <RuleForm board={board} onDone={() => setAdding(false)} />}
      {manager && <Starters board={board} onOpenItem={onOpenItem} />}
      {rules.map((r) => (
        <article key={r.id} className={`grid gap-1.5 rounded-lg border p-3 ${r.enabled ? '' : 'opacity-70'}`}>
          <div className="flex items-start justify-between gap-2">
            <span className="text-[13px] font-semibold">{r.name}</span>
            <ToneBadge tone={!r.enabled ? 'neutral' : r.mode === 'auto' && r.action === 'draft_email' ? 'ok' : 'info'}>
              {!r.enabled ? 'Off' : r.action === 'notify' ? 'Tells people' : r.mode === 'auto' ? 'Does it itself' : 'Asks first'}
            </ToneBadge>
          </div>
          <p className="text-[13px] text-pretty">{r.description}</p>
          <ScoreLine score={scores.get(`recipe_${r.id}`)} />
          {manager && (
            <div className="flex flex-wrap gap-2">
              <Button size="xs" variant="outline" onClick={() => change.mutate({ id: r.id, patch: { enabled: !r.enabled } })}>
                {r.enabled ? 'Switch off' : 'Switch on'}
              </Button>
              {removing === r.id ? (
                <>
                  <Button size="xs" variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate(r.id)}>
                    Remove it
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => setRemoving(null)}>
                    Keep it
                  </Button>
                </>
              ) : (
                <Button size="xs" variant="ghost" onClick={() => setRemoving(r.id)}>
                  Remove
                </Button>
              )}
            </div>
          )}
        </article>
      ))}
      {manager && hasDates && (
        <div className="grid gap-2">
          <div className="flex flex-wrap gap-2">
            <Button size="xs" variant="outline" disabled={checkDates.isPending} onClick={() => checkDates.mutate(true)}>
              {checkDates.isPending && checkDates.variables ? <Loader2 className="animate-spin" /> : <Play />}
              Preview the date rules
            </Button>
            {checkDates.data?.dry_run && checkDates.data.did.length > 0 && (
              <Button size="xs" disabled={checkDates.isPending} onClick={() => checkDates.mutate(false)}>
                Run them now
              </Button>
            )}
          </div>
          {checkDates.data && <DidSummary dryRun={checkDates.data.dry_run} did={checkDates.data.did} />}
        </div>
      )}
    </section>
  );
}

function DidSummary({ dryRun, did }: { dryRun: boolean; did: Did[] }) {
  return (
    <div className="grid gap-1 rounded-md bg-muted/40 p-2 text-xs">
      <span className="font-medium">
        {did.length ? (dryRun ? 'Every board’s date rules would:' : 'Done:') : 'Nothing is due on any date rule.'}
      </span>
      <DidList did={did} max={10} />
    </div>
  );
}

/** What an automation did, or would do: "what (outcome)", the first few. */
export function DidList({ did, max = 8 }: { did: Did[]; max?: number }) {
  return (
    <ul className="grid gap-0.5 text-xs text-muted-foreground">
      {did.slice(0, max).map((d, i) => (
        <li key={i}>
          {d.what} <span className="text-foreground">({d.outcome})</span>
        </li>
      ))}
      {did.length > max && <li>and {did.length - max} more</li>}
    </ul>
  );
}

// An RFQ's status change is the tracker's own status, not the board's label, so
// "the status becomes" rules on the RFQ board are written against these.
const RFQ_STATUSES = ['NEW', 'ACKNOWLEDGED', 'CLARIFYING', 'WAITING_VENDOR', 'PRICING', 'QUOTE_SENT', 'WON', 'LOST', 'NO_BID'];

const WHO = 'owner';

/** A rule in the board's own words: its status labels and date columns are offered, not typed. */
function RuleForm({ board, onDone }: { board: Board; onDone: () => void }) {
  const { add } = useRules(board.board_id);
  // Rules watch the board's main status column only; labels from other status columns would never match.
  const labels = Object.keys(board.columns.find((c) => c.id === board.status_column)?.labels ?? {});
  const changes: [string, string][] = board.role === 'rfqs' ? RFQ_STATUSES.map((s) => [s, words(s)]) : labels.map((l) => [l, l]);
  const dateColumns = board.columns.filter((c) => c.type === 'date' || c.type === 'timeline');
  const [rule, setRule] = useState<RuleIn>({
    name: '',
    board_role: board.role as RuleIn['board_role'],
    trigger: changes.length ? 'status_is' : 'date_within',
    value: changes[0]?.[0] ?? '3',
    column: dateColumns[0]?.title,
    action: 'notify',
    recipients: WHO,
    // Only vendor and RFQ rows have someone to write to by role; elsewhere an address is typed.
    email_to: board.role === 'rfqs' ? 'customer' : board.role === 'vendors' ? 'vendor' : '',
    mode: 'suggest',
  });
  const [statusFor, setStatusFor] = useState(labels[0] ?? '');
  const set = (patch: RuleIn) => setRule((r) => ({ ...r, ...patch }));
  const email = rule.action === 'draft_email';
  const address = email && rule.email_to !== 'vendor' && rule.email_to !== 'customer';

  return (
    <form
      className="grid gap-3 rounded-lg border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        const body: RuleIn = { ...rule, subject: rule.subject || undefined, body: rule.body || undefined };
        if (body.trigger === 'status_is') delete body.column;
        if (body.trigger === 'status_for') body.column = statusFor;
        if (!email) {
          delete body.email_to;
          delete body.mode;
        }
        add.mutate(body, { onSuccess: onDone });
      }}
    >
      <Field label="Name">
        <Input required value={rule.name ?? ''} onChange={(e) => set({ name: e.target.value })} placeholder="Ship date close" />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="When">
          <NativeSelect
            value={rule.trigger}
            onChange={(e) => {
              const trigger = e.target.value as Rule['trigger'];
              set({ trigger, value: trigger === 'status_is' ? (changes[0]?.[0] ?? '') : '3' });
            }}
          >
            {changes.length > 0 && <option value="status_is">the status becomes</option>}
            {labels.length > 0 && <option value="status_for">an item sits in a status</option>}
            {dateColumns.length > 0 && <option value="date_within">a date is this close</option>}
          </NativeSelect>
        </Field>
        {rule.trigger === 'status_for' ? (
          <div className="grid grid-cols-[1fr_6rem] gap-2">
            <Field label="Status">
              <NativeSelect value={statusFor} onChange={(e) => setStatusFor(e.target.value)}>
                {labels.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="For days">
              <Input type="number" min={1} max={365} required value={rule.value} onChange={(e) => set({ value: e.target.value })} />
            </Field>
          </div>
        ) : rule.trigger === 'status_is' ? (
          <Field label="Status">
            <NativeSelect value={rule.value} onChange={(e) => set({ value: e.target.value })}>
              {changes.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </NativeSelect>
          </Field>
        ) : (
          <div className="grid grid-cols-[1fr_6rem] gap-2">
            <Field label="Date">
              <NativeSelect value={rule.column ?? ''} onChange={(e) => set({ column: e.target.value })}>
                {dateColumns.map((c) => (
                  <option key={c.id} value={c.title}>
                    {c.title}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Days before">
              <Input type="number" min={-60} max={365} required value={rule.value} onChange={(e) => set({ value: e.target.value })} />
            </Field>
          </div>
        )}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Then">
          <NativeSelect value={rule.action} onChange={(e) => set({ action: e.target.value as Rule['action'] })}>
            <option value="notify">tell people</option>
            <option value="draft_email">draft an email</option>
          </NativeSelect>
        </Field>
        <Field label={email ? 'Who reviews it' : 'Who'} hint="owner, leadership, reviewers, or email addresses">
          <Input value={rule.recipients ?? ''} onChange={(e) => set({ recipients: e.target.value })} />
        </Field>
      </div>
      {email && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Email to">
            <NativeSelect
              value={address ? 'address' : (rule.email_to ?? '')}
              onChange={(e) => set({ email_to: e.target.value === 'address' ? '' : e.target.value })}
            >
              {board.role === 'vendors' && <option value="vendor">the vendor</option>}
              {board.role === 'rfqs' && <option value="customer">the customer</option>}
              <option value="address">an address</option>
            </NativeSelect>
          </Field>
          {address ? (
            <Field label="Address">
              <Input type="email" required value={rule.email_to ?? ''} onChange={(e) => set({ email_to: e.target.value })} />
            </Field>
          ) : (
            <Field label="Sending">
              <NativeSelect value={rule.mode} onChange={(e) => set({ mode: e.target.value as Rule['mode'] })}>
                <option value="suggest">a person reads it and sends it</option>
                <option value="auto">send it straight away</option>
              </NativeSelect>
            </Field>
          )}
        </div>
      )}
      <Field label="Subject (optional)" hint="Use {item}, {board}, {status}, {date}, {days} or any column title in braces.">
        <Input value={rule.subject ?? ''} onChange={(e) => set({ subject: e.target.value })} placeholder="{item}: {status}" />
      </Field>
      <Field label="Message (optional)">
        <Textarea rows={3} value={rule.body ?? ''} onChange={(e) => set({ body: e.target.value })} />
        <DictateButton className="justify-self-start" onText={(t) => set({ body: addDictation(rule.body ?? '', t) })} />
      </Field>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={add.isPending}>
          {add.isPending && <Loader2 className="animate-spin" />}
          Add the rule
        </Button>
      </div>
    </form>
  );
}

/** Allied's ready-made rules for this board: the board fact behind each, and what it would act on today. */
function Starters({ board, onOpenItem }: { board: Board; onOpenItem?: (itemId: string) => void }) {
  const starters = useStarters(true);
  const add = useAddStarter(board.board_id);
  const [who, setWho] = useState<Record<string, string>>({});
  const mine = (starters.data ?? []).filter(
    (s) => !s.added && (s.board_id ? s.board_id === board.board_id : s.recipe.board_role === board.role),
  );
  if (starters.isPending) return <p className="text-xs text-muted-foreground">Looking for rules that suit this board…</p>;
  if (!mine.length) return null;

  return (
    <div className="grid gap-2">
      <PanelHeading>Suggested for this board</PanelHeading>
      {mine.map((s: Starter) => {
        const needsWho = s.needs === 'recipients';
        const today = s.today ?? [];
        return (
          <article key={s.slug} className="grid gap-1.5 rounded-lg border border-dashed p-3">
            <span className="text-[13px] font-semibold">{s.recipe.name}</span>
            <p className="text-[13px] text-pretty">{s.recipe.description}</p>
            <p className="text-xs text-muted-foreground">
              {s.why}
              {s.saves && <> {s.saves}</>}
            </p>
            {today.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                  Would act on {today.length} item{today.length === 1 ? '' : 's'} today
                </summary>
                <ul className="mt-1 grid max-h-40 gap-0.5 overflow-auto">
                  {today.map((t) => (
                    <li key={t.item_id}>
                      {onOpenItem ? (
                        <button
                          type="button"
                          className="text-left underline-offset-2 hover:underline"
                          onClick={() => onOpenItem(t.item_id)}
                        >
                          {t.item}
                        </button>
                      ) : (
                        t.item
                      )}
                      {t.detail && <span className="text-muted-foreground"> · {t.detail}</span>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                add.mutate({ slug: s.slug, overrides: needsWho ? { recipients: who[s.slug] } : undefined });
              }}
            >
              {needsWho && (
                <Input
                  required
                  aria-label="Who gets it"
                  placeholder="Who gets it: emails, owner or leadership"
                  className="h-7 min-w-0 flex-1 text-xs"
                  value={who[s.slug] ?? ''}
                  onChange={(e) => setWho((w) => ({ ...w, [s.slug]: e.target.value }))}
                />
              )}
              <Button size="xs" type="submit" variant="outline" disabled={add.isPending}>
                {add.isPending && add.variables?.slug === s.slug ? <Loader2 className="animate-spin" /> : <Plus />}
                Add this rule
              </Button>
            </form>
          </article>
        );
      })}
    </div>
  );
}
