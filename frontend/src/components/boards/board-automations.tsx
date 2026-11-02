'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Loader2, Play, RefreshCw, Zap } from 'lucide-react';
import { toast } from 'sonner';
import { PanelHeading } from '@/components/data/filters';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { CopyButton } from '@/components/tools/ai-output';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useAuth } from '@/context/AuthContext';
import { Input } from '@/components/ui/input';
import { day, isoDay, since } from '@/lib/format';
import { cn } from '@/lib/utils';
import {
  needsAction,
  useDecide,
  useDraftNow,
  useFollowUpWait,
  useRunChecks,
  useRunPlay,
  useScorecard,
  type Automations,
  type Did,
  type Draft,
  type FollowUp,
  type Mode,
  type Recipe,
  type RunReport,
  type Score,
} from './use-automations';
import { BoardRules, DidList, ScoreLine } from './board-rules';
import type { Board } from './use-boards';

const MODE: Record<Mode, { label: string; tone: Tone; hint: string }> = {
  off: { label: 'Off', tone: 'neutral', hint: 'Does nothing.' },
  suggest: { label: 'Asks first', tone: 'info', hint: 'Drafts the message; a person reads it and sends it.' },
  auto: { label: 'Does it itself', tone: 'ok', hint: 'Acts straight away. Email still only goes out when sending is switched on.' },
};

const KIND: Record<Recipe['kind'], string> = { reaction: 'When something changes', play: 'On a timer', check: 'Schedule checks' };

// The drafts that are a vendor follow-up; one waiting holds back "Follow up now".
const FOLLOW_UP_PLAYS = new Set(['vendor_chaser', 'vendor_late_chase']);

const DRAFT_STATE: Record<Draft['status'], { label: string; tone: Tone }> = {
  PENDING: { label: 'Waiting to send', tone: 'warn' },
  RUNNING: { label: 'Sending', tone: 'info' },
  DONE: { label: 'Sent', tone: 'ok' },
  FAILED: { label: 'Failed', tone: 'bad' },
  CANCELLED: { label: 'Cancelled', tone: 'neutral' },
  EXPIRED: { label: 'Expired', tone: 'neutral' },
};

/* ------------------------------------------------------------ the button */

/** monday's "Automate" button: opens the board's recipes. The count is drafts waiting for someone. */
export function AutomateButton({ data, onOpen }: { data: Automations | undefined; onOpen: () => void }) {
  const on = data?.recipes.filter((r) => r.mode !== 'off').length ?? 0;
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={onOpen}
      aria-label={`Automate: ${on} on${data?.waiting ? `, ${data.waiting} draft${data.waiting === 1 ? '' : 's'} waiting` : ''}`}
    >
      <Zap className="text-warn" />
      Automate
      {data && <span className="text-muted-foreground tabular-nums">/ {on}</span>}
      {data && data.waiting > 0 && <ToneBadge tone="warn">{data.waiting} to send</ToneBadge>}
    </Button>
  );
}

/** The chip on a row or card when an automation needs a person there. */
export function AutomationChip({ data, itemId }: { data: Automations | undefined; itemId: string }) {
  const { hasRole } = useAuth();
  const n = needsAction(data, itemId, hasRole('OPERATOR'));
  if (!n.any) return null;
  const manual = n.due === n.manual;
  return (
    <ToneBadge tone={n.waiting ? 'warn' : 'info'}>
      <Zap className="size-3" aria-hidden />
      {n.waiting ? `${n.waiting} to send` : manual ? 'Send by hand' : 'Can draft'}
    </ToneBadge>
  );
}

/* ------------------------------------------------------------ the panel */

/** Every automation that acts on this board, in monday's "When this, then that" words, each one runnable. */
export function AutomatePanel({
  open,
  onClose,
  board,
  data,
  onOpenItem,
}: {
  open: boolean;
  onClose: () => void;
  board: Board;
  data: Automations | undefined;
  /** Close the panel and open this item's card. */
  onOpenItem?: (itemId: string) => void;
}) {
  const kinds = (['reaction', 'play', 'check'] as const).filter((k) => data?.recipes.some((r) => r.kind === k));
  const { hasRole } = useAuth();
  const scorecard = useScorecard(open && hasRole('MANAGER'));
  const scores = new Map((scorecard.data?.automations ?? []).map((s) => [s.name, s]));
  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      <SheetContent side="right" className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-lg">
        <SheetHeader className="border-b">
          <SheetTitle className="flex items-center gap-2">
            <Zap className="size-4 text-warn" aria-hidden /> Automations on {board.name}
          </SheetTitle>
          <SheetDescription>
            These run in Allied’s app, never in monday, and nothing is written to monday. Messages to people outside Allied wait for someone
            to send them unless a recipe does it itself.
          </SheetDescription>
        </SheetHeader>
        <div className="grid min-h-0 gap-5 overflow-y-auto p-4">
          {!data ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <>
              {!data.enabled && (
                <p className="rounded-md border border-warn/40 bg-warn-soft p-3 text-xs text-warn">
                  The autopilot is switched off (AUTOPILOT_ENABLED), so only the schedule checks run. Drafts can still be made from a row.
                </p>
              )}
              {kinds.map((k) => (
                <section key={k} className="grid gap-2">
                  <PanelHeading>{KIND[k]}</PanelHeading>
                  {data.recipes
                    .filter((r) => r.kind === k)
                    .map((r) => (
                      <RecipeCard key={r.name} boardId={board.board_id} recipe={r} score={scores.get(r.name)} />
                    ))}
                </section>
              ))}
              <BoardRules board={board} rules={data.rules ?? []} scores={scores} onOpenItem={onOpenItem} />
              {data.recent.length > 0 && (
                <section className="grid gap-2">
                  <PanelHeading>Lately on this board</PanelHeading>
                  <ul className="grid gap-1.5 text-xs">
                    {data.recent.map((e, i) => (
                      <li key={`${e.at}-${i}`} className="grid gap-0.5 rounded-md border p-2">
                        <span className="flex justify-between gap-2">
                          <span className="font-medium">{e.summary || e.event}</span>
                          <span className="shrink-0 text-muted-foreground">{since(e.at)}</span>
                        </span>
                        {e.reactions
                          .flatMap((x) => x.did ?? [])
                          .map((d, j) => (
                            <span key={j} className="text-muted-foreground">
                              {d.what}: {d.outcome}
                            </span>
                          ))}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {hasRole('MANAGER') && (
                <Link
                  href="/health?tab=automation"
                  className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                >
                  Every automation across the app, and mail it has written
                </Link>
              )}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function RecipeCard({ boardId, recipe, score }: { boardId: string; recipe: Recipe; score?: Score }) {
  const { hasRole } = useAuth();
  const mode = MODE[recipe.mode];
  const run = useRunPlay(boardId);
  const checks = useRunChecks();
  const [preview, setPreview] = useState<RunReport | null>(null);
  const report = preview?.plays[recipe.name];
  const ran = run.data && !run.data.dry_run ? run.data.plays[recipe.name] : null;

  return (
    <article className={cn('grid gap-2 rounded-lg border p-3', recipe.mode === 'off' && 'opacity-70')}>
      <p className="text-[13px] text-pretty">
        <span className="font-semibold">{recipe.when}</span>, then <span className="font-semibold">{recipe.then}</span>.
      </p>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span title={mode.hint}>
          <ToneBadge tone={mode.tone}>{mode.label}</ToneBadge>
        </span>
        {recipe.last ? (
          <span>
            Last {recipe.kind === 'reaction' ? 'fired' : 'ran'} {since(recipe.last.at)}
            {recipe.last.error
              ? `, failed: ${recipe.last.error}`
              : `: ${recipe.last.did.length} thing${recipe.last.did.length === 1 ? '' : 's'}`}
          </span>
        ) : (
          recipe.kind !== 'check' && <span>Not run since the app started</span>
        )}
      </div>
      {recipe.last && recipe.last.did.length > 0 && <DidList did={recipe.last.did} />}
      <ScoreLine score={score} />

      {recipe.can_preview && hasRole('ADMIN') && (
        <div className="grid gap-2">
          <div className="flex flex-wrap gap-2">
            <Button
              size="xs"
              variant="outline"
              disabled={run.isPending}
              onClick={() => run.mutate({ play: recipe.name, dryRun: true }, { onSuccess: (r) => setPreview(r) })}
            >
              {run.isPending && run.variables?.dryRun ? <Loader2 className="animate-spin" /> : <Play />}
              Preview
            </Button>
            {preview && (
              <Button
                size="xs"
                disabled={run.isPending || recipe.mode === 'off'}
                title={recipe.mode === 'off' ? 'This recipe is off' : undefined}
                onClick={() =>
                  run.mutate(
                    { play: recipe.name, dryRun: false },
                    {
                      onSuccess: (r) => {
                        setPreview(null);
                        const p = r.plays[recipe.name];
                        toast(p?.skipped ? `Not run: ${p.skipped}.` : `Done: ${p?.did.length ?? 0} thing(s).`);
                      },
                    },
                  )
                }
              >
                {run.isPending && !run.variables?.dryRun && <Loader2 className="animate-spin" />}
                Run it now
              </Button>
            )}
          </div>
          {report && (
            <div className="grid gap-1 rounded-md bg-muted/40 p-2 text-xs">
              <span className="font-medium">
                {report.error ? `Couldn’t run: ${report.error}` : report.did.length ? 'Running now would:' : 'Nothing to do right now.'}
              </span>
              <DidList did={report.did} />
            </div>
          )}
          {ran && !preview && ran.did.length > 0 && <DidList did={ran.did} />}
        </div>
      )}
      {recipe.kind === 'check' && hasRole('OPERATOR') && (
        <div>
          <Button size="xs" variant="outline" disabled={checks.isPending} onClick={() => checks.mutate()}>
            {checks.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Run the checks now
          </Button>
        </div>
      )}
    </article>
  );
}

/* ------------------------------------------------------------ one item */

/** On the item card: drafts to send or cancel, things to draft now, and messages to send by hand. */
export function ItemAutomations({ boardId, itemId, data }: { boardId: string; itemId: string; data: Automations | undefined }) {
  const { hasRole } = useAuth();
  const decide = useDecide(boardId);
  const draft = useDraftNow(boardId);
  if (!data) return null;
  const drafts = data.drafts[itemId] ?? [];
  const due = data.due[itemId] ?? [];
  const on = data.recipes.filter((r) => r.mode !== 'off').length;
  const follow = data.follow_ups?.[itemId];
  const followUpWaiting = drafts.some((d) => d.status === 'PENDING' && d.play != null && FOLLOW_UP_PLAYS.has(d.play));

  return (
    <section className="grid gap-2" aria-label="Automations">
      <PanelHeading>
        <span className="inline-flex items-center gap-1">
          <Zap className="size-3 text-warn" aria-hidden /> Automations
        </span>
      </PanelHeading>
      {follow && (
        <VendorFollowUp
          key={follow.job_id}
          boardId={boardId}
          follow={follow}
          canAct={hasRole('OPERATOR')}
          pending={followUpWaiting}
          drafting={draft.isPending && draft.variables?.key === follow.now_key}
          onFollowUpNow={() => follow.now_key && draft.mutate({ itemId, key: follow.now_key })}
        />
      )}
      {!drafts.length && !due.length && !follow && (
        <p className="text-xs text-muted-foreground">
          Nothing waiting on this item. {on} automation{on === 1 ? '' : 's'} watch this board.
        </p>
      )}
      {drafts.map((d) => (
        <article key={d.id} className="grid gap-1.5 rounded-lg border p-2.5 text-[13px]">
          <div className="flex items-start justify-between gap-2">
            <span className="font-medium text-pretty">{d.summary}</span>
            <ToneBadge tone={DRAFT_STATE[d.status].tone}>{DRAFT_STATE[d.status].label}</ToneBadge>
          </div>
          {(d.to || d.subject) && (
            <span className="text-xs text-muted-foreground">
              {d.to && <>To {d.to}</>}
              {d.subject && <> · {d.subject}</>}
            </span>
          )}
          {d.body && (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Read the message</summary>
              <pre className="mt-1 max-h-56 overflow-auto rounded-md bg-muted/40 p-2 font-sans whitespace-pre-wrap">{d.body}</pre>
            </details>
          )}
          {d.status === 'PENDING' ? (
            d.yours ? (
              <div className="flex justify-end gap-2">
                <Button size="xs" variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate({ id: d.id, confirm: false })}>
                  Cancel
                </Button>
                <Button size="xs" disabled={decide.isPending} onClick={() => decide.mutate({ id: d.id, confirm: true })}>
                  {decide.isPending && decide.variables?.id === d.id && decide.variables.confirm && <Loader2 className="animate-spin" />}
                  Send it
                </Button>
              </div>
            ) : (
              <span className="text-xs text-muted-foreground">Waiting for {d.reviewer} to send or cancel it.</span>
            )
          ) : (
            d.decided_at && (
              <span className="text-xs text-muted-foreground">
                {DRAFT_STATE[d.status].label} {since(d.decided_at)}
              </span>
            )
          )}
        </article>
      ))}
      {due.map((d, i) =>
        d.manual ? (
          <ManualMessage key={`manual-${i}`} summary={d.summary} body={d.body ?? ''} />
        ) : (
          <article key={d.key} className="flex items-center justify-between gap-2 rounded-lg border border-dashed p-2.5 text-[13px]">
            <span className="text-pretty">{d.summary}</span>
            {hasRole('OPERATOR') && (
              <Button
                size="xs"
                variant="outline"
                className="shrink-0"
                disabled={draft.isPending}
                onClick={() => draft.mutate({ itemId, key: d.key! })}
              >
                {draft.isPending && draft.variables?.key === d.key ? <Loader2 className="animate-spin" /> : <Zap />}
                Draft it
              </Button>
            )}
          </article>
        ),
      )}
    </section>
  );
}

/**
 * When this order's vendor is followed up: once, on its own, when it hasn't shipped its wait after
 * the PO; after that only when staff press Follow up now. The wait can be changed on the order.
 */
function VendorFollowUp({
  boardId,
  follow,
  canAct,
  pending,
  drafting,
  onFollowUpNow,
}: {
  boardId: string;
  follow: FollowUp;
  canAct: boolean;
  pending: boolean;
  drafting: boolean;
  onFollowUpNow: () => void;
}) {
  const wait = useFollowUpWait(boardId);
  const [editing, setEditing] = useState(false);
  const [days, setDays] = useState(String(follow.manual_days ?? follow.wait_days));
  const reached = follow.first <= isoDay();

  return (
    <article className="grid gap-1.5 rounded-lg border p-2.5 text-[13px]">
      <span className="font-medium">
        {follow.started ? 'Vendor followed up' : reached ? 'Vendor follow-up due' : `Vendor follow-up on ${day(follow.first)}`}
      </span>
      <span className="text-xs text-muted-foreground">
        {follow.wait_days} days after the PO ({follow.wait_basis}).
        {follow.last && <> Last sent {day(follow.last)}.</>}
        {follow.drafted && <> A follow-up is waiting to send.</>}
        {follow.started && !follow.drafted && ' Further follow-ups are sent when you ask.'}
      </span>
      {canAct && (
        <div className="flex flex-wrap items-center gap-2">
          {follow.now_key && follow.started && (
            <Button
              size="xs"
              disabled={drafting || pending}
              title={pending ? 'A follow-up is already waiting to send' : undefined}
              onClick={onFollowUpNow}
            >
              {drafting ? <Loader2 className="animate-spin" /> : <Zap />}
              Follow up now
            </Button>
          )}
          {editing ? (
            <form
              className="flex items-center gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                wait.mutate({ jobId: follow.job_id, days: Number(days) }, { onSuccess: () => setEditing(false) });
              }}
            >
              <Input
                type="number"
                min={0}
                max={365}
                required
                aria-label="Days after the PO"
                className="h-7 w-20 text-xs"
                value={days}
                onChange={(e) => setDays(e.target.value)}
              />
              <span className="text-xs text-muted-foreground">days after the PO (0 = usual)</span>
              <Button size="xs" type="submit" disabled={wait.isPending}>
                Save
              </Button>
              <Button size="xs" type="button" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </form>
          ) : (
            <Button size="xs" variant="ghost" onClick={() => setEditing(true)}>
              Change the wait
            </Button>
          )}
        </div>
      )}
    </article>
  );
}

function ManualMessage({ summary, body }: { summary: string; body: string }) {
  return (
    <article className="grid gap-1.5 rounded-lg border border-dashed p-2.5 text-[13px]">
      <span className="text-pretty">{summary}</span>
      <CopyButton text={body} label="Copy the message" />
      <pre className="max-h-40 overflow-auto rounded-md bg-muted/40 p-2 font-sans text-xs whitespace-pre-wrap">{body}</pre>
    </article>
  );
}
