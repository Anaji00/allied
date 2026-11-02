'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ChevronRight, Download, Loader2 } from 'lucide-react';
import { Facts } from '@/components/data/detail-panel';
import { Field, PanelHeading } from '@/components/data/filters';
import { ToneBadge } from '@/components/data/tone-badge';
import { DictateButton, addDictation } from '@/components/data/voice';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { downloadSubmissionFile, reviewSubmission, submitDesignDocs } from '@/lib/api';
import { day, dateTime, person } from '@/lib/format';
import { cn } from '@/lib/utils';
import { SUBMISSION, fileSize, milestoneState, type Milestone, type Project, type Submission } from './use-projects';

const names = (emails: string[] | undefined, me: string, empty = 'Not set') => (emails ?? []).map((e) => person(e, me)).join(', ') || empty;

export function ProjectDetail({
  project: p,
  leadership,
  openMilestone,
  onToggleMilestone,
}: {
  project: Project;
  leadership: string[];
  openMilestone: string | null;
  onToggleMilestone: (id: string) => void;
}) {
  const { employeeEmail } = useAuth();
  const me = (employeeEmail ?? '').trim().toLowerCase();
  const done = p.milestones.filter((m) => m.state === 'done').length;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-1.5">
        {p.done && <ToneBadge tone="ok">Done</ToneBadge>}
        {p.behind > 0 && <ToneBadge tone="bad">{p.behind} late</ToneBadge>}
        {p.overdue && <ToneBadge tone="bad">Past its due date</ToneBadge>}
        {!p.done && !p.behind && !p.overdue && <ToneBadge>On track</ToneBadge>}
      </div>
      <Facts
        items={[
          ['Owner', names(p.owner_emails, me)],
          ['Due', day(p.due_date, { weekday: true })],
          ['Milestones', `${done} of ${p.milestones.length} done`],
          ['monday status', p.status || '–'],
        ]}
      />

      <div className="grid gap-2">
        <PanelHeading>Milestones</PanelHeading>
        {p.milestones.length === 0 ? (
          <p className="text-sm text-muted-foreground">No milestones on this project yet.</p>
        ) : (
          <ul className="grid gap-1.5">
            {p.milestones.map((m) => (
              <MilestoneItem
                key={m.item_id}
                milestone={m}
                project={p}
                me={me}
                leadership={leadership}
                open={openMilestone === m.item_id}
                onToggle={() => onToggleMilestone(m.item_id)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** One job on a board laid out by group: its own facts and design documents, without the group around it. */
export function JobDetail({ milestone: m, project, leadership }: { milestone: Milestone; project: Project; leadership: string[] }) {
  const { employeeEmail } = useAuth();
  const me = (employeeEmail ?? '').trim().toLowerCase();
  const state = milestoneState(m);
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-1.5">
        <ToneBadge tone={state.tone}>{state.label}</ToneBadge>
        {m.missing_docs && <ToneBadge tone="warn">Design docs needed</ToneBadge>}
      </div>
      {/* Owner, reviewers, due date and monday status are in the body; the group is under the title. */}
      <MilestoneBody milestone={m} project={project} me={me} leadership={leadership} />
    </div>
  );
}

function MilestoneItem({
  milestone: m,
  project,
  me,
  leadership,
  open,
  onToggle,
}: {
  milestone: Milestone;
  project: Project;
  me: string;
  leadership: string[];
  open: boolean;
  onToggle: () => void;
}) {
  const state = milestoneState(m);
  const panelId = `milestone-${m.item_id}`;
  return (
    <li className={cn('rounded-md border', open && 'border-foreground/30')}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        ref={(el) => {
          // An alert's link lands on the milestone; bring it into view once.
          if (el && open && !el.dataset.seen) {
            el.dataset.seen = '1';
            el.scrollIntoView({ block: 'nearest' });
          }
        }}
        className="flex w-full items-start gap-2 p-2.5 text-left text-[13px]"
      >
        <ChevronRight className={cn('mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')} />
        <span className="grid min-w-0 flex-1 gap-0.5">
          <span className="font-medium">{m.name}</span>
          <span className="text-xs text-muted-foreground">
            {names(m.owner_emails, me, 'No owner')} · due {day(m.due_date)}
            {m.latest_submission && ` · version ${m.latest_submission.version}`}
          </span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1">
          <ToneBadge tone={state.tone}>{state.label}</ToneBadge>
          {m.missing_docs && <ToneBadge tone="warn">Docs needed</ToneBadge>}
        </span>
      </button>
      {open && (
        <div id={panelId} className="grid gap-3 border-t p-3">
          <MilestoneBody milestone={m} project={project} me={me} leadership={leadership} />
        </div>
      )}
    </li>
  );
}

function MilestoneBody({
  milestone: m,
  project,
  me,
  leadership,
}: {
  milestone: Milestone;
  project: Project;
  me: string;
  leadership: string[];
}) {
  const { hasRole } = useAuth();
  const canOperate = hasRole('OPERATOR');
  const latest = m.latest_submission;
  const waiting = latest?.status === 'SUBMITTED';
  const mine = waiting && (latest?.submitted_by ?? '').toLowerCase() === me;
  // The API takes reviews from operators and above, and never from the sender.
  const canReview = waiting && canOperate && Boolean(me) && !mine;
  const canSubmit = canOperate && m.state !== 'done' && !waiting;

  const reviewers = m.reviewer_emails?.length
    ? names(m.reviewer_emails, me)
    : project.owner_emails?.length
      ? `Project owner: ${names(project.owner_emails, me)}`
      : `Leadership: ${names(leadership, me, 'not set')}`;

  return (
    <>
      <Facts
        items={[
          ['Owner', names(m.owner_emails, me)],
          ['Reviewers', reviewers],
          ['Due', day(m.due_date, { weekday: true })],
          ['monday status', m.status || '–'],
        ]}
      />
      {canReview && latest && <ReviewForm submission={latest} />}
      {mine && <p className="text-sm text-muted-foreground">Your version {latest?.version} is waiting for someone else to review it.</p>}
      {waiting && !mine && !canReview && <p className="text-sm text-muted-foreground">Version {latest?.version} is waiting for review.</p>}
      {canSubmit && <SubmitForm milestone={m} />}

      <div className="grid gap-2">
        <PanelHeading>Design documents sent</PanelHeading>
        {m.submissions.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing sent yet.{m.needs_submission && ' This milestone needs design documents.'}
          </p>
        ) : (
          <ol className="grid gap-2">
            {m.submissions.map((s) => (
              <SubmissionItem key={s.id} submission={s} me={me} />
            ))}
          </ol>
        )}
      </div>
    </>
  );
}

function SubmissionItem({ submission: s, me }: { submission: Submission; me: string }) {
  const state = SUBMISSION[s.status] ?? { label: s.status, tone: 'neutral' as const };
  const download = useMutation({
    mutationFn: ({ index, name }: { index: number; name: string }) => downloadSubmissionFile(s.id, index, name),
    onError: (e) => toast.error('Couldn’t download the file', { description: e.message }),
  });
  return (
    <li className="grid gap-1.5 rounded-md bg-muted/40 p-2.5 text-[13px]">
      <div className="flex items-center gap-2">
        <span className="font-medium">Version {s.version}</span>
        <ToneBadge tone={state.tone} className="ml-auto">
          {state.label}
        </ToneBadge>
      </div>
      <p className="text-xs text-muted-foreground">
        Sent by {person(s.submitted_by, me) || 'someone'}, {dateTime(s.submitted_at)}
      </p>
      {s.note && <p className="whitespace-pre-line">{s.note}</p>}
      {s.files.length > 0 && (
        <ul className="grid gap-1">
          {s.files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate">
                {f.name} <span className="text-xs text-muted-foreground">{fileSize(f.size)}</span>
              </span>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Download ${f.name}`}
                disabled={download.isPending}
                onClick={() => download.mutate({ index: i, name: f.name })}
              >
                {download.isPending && download.variables?.index === i ? <Loader2 className="animate-spin" /> : <Download />}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {s.reviewed_by && (
        <div className={cn('grid gap-0.5 border-l-2 pl-2', s.status === 'CHANGES_REQUESTED' ? 'border-warn' : 'border-ok')}>
          <p className="text-xs text-muted-foreground">
            {s.status === 'APPROVED' ? 'Approved' : 'Reviewed'} by {person(s.reviewed_by, me)}, {dateTime(s.reviewed_at)}
          </p>
          {s.review_note && <p className="whitespace-pre-line">{s.review_note}</p>}
        </div>
      )}
    </li>
  );
}

function ReviewForm({ submission }: { submission: Submission }) {
  const client = useQueryClient();
  const [note, setNote] = useState('');
  const [needNote, setNeedNote] = useState(false);
  const review = useMutation({
    mutationFn: (approve: boolean) => reviewSubmission(submission.id, approve, note.trim()),
    onSuccess: (_res, approve) => {
      toast.success(approve ? `Approved version ${submission.version}` : `Sent version ${submission.version} back with changes`);
      void client.invalidateQueries({ queryKey: ['projects'] });
    },
    onError: (e) => toast.error('The review wasn’t saved', { description: e.message }),
  });

  return (
    <div className="grid gap-2 rounded-md border p-3">
      <PanelHeading>Review version {submission.version}</PanelHeading>
      <Field label="Note" hint="Optional when approving. When asking for changes, say what to change.">
        <Textarea
          rows={3}
          value={note}
          aria-invalid={needNote || undefined}
          onChange={(e) => {
            setNote(e.target.value);
            setNeedNote(false);
          }}
        />
        <DictateButton
          className="justify-self-start"
          onText={(t) => {
            setNote((cur) => addDictation(cur, t));
            setNeedNote(false);
          }}
        />
      </Field>
      {needNote && (
        <p role="alert" className="text-xs text-bad">
          Say what needs to change before sending it back.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={review.isPending} onClick={() => review.mutate(true)}>
          {review.isPending && review.variables && <Loader2 className="animate-spin" />}
          Approve
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={review.isPending}
          onClick={() => (note.trim() ? review.mutate(false) : setNeedNote(true))}
        >
          {review.isPending && review.variables === false && <Loader2 className="animate-spin" />}
          Request changes
        </Button>
      </div>
    </div>
  );
}

function SubmitForm({ milestone }: { milestone: Milestone }) {
  const client = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const [note, setNote] = useState('');
  const [inputKey, setInputKey] = useState(0);
  const again = milestone.latest_submission?.status === 'CHANGES_REQUESTED';

  const send = useMutation({
    mutationFn: () => submitDesignDocs(milestone.item_id, files, note.trim()),
    onSuccess: () => {
      toast.success('Sent for review');
      setFiles([]);
      setNote('');
      setInputKey((k) => k + 1);
      void client.invalidateQueries({ queryKey: ['projects'] });
    },
    onError: (e) => toast.error('The documents weren’t sent', { description: e.message }),
  });

  return (
    <form
      className="grid gap-2 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (files.length) send.mutate();
      }}
    >
      <PanelHeading>{again ? 'Send a new version' : 'Send design documents for review'}</PanelHeading>
      <Field label="Files" hint={files.length ? files.map((f) => f.name).join(', ') : 'You can choose more than one.'}>
        <Input key={inputKey} type="file" multiple onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
      </Field>
      <Field label="Note (optional)">
        <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything the reviewer should know" />
        <DictateButton className="justify-self-start" onText={(t) => setNote((cur) => addDictation(cur, t))} />
      </Field>
      <Button type="submit" size="sm" className="justify-self-start" disabled={!files.length || send.isPending}>
        {send.isPending && <Loader2 className="animate-spin" />}
        Send for review
      </Button>
    </form>
  );
}
