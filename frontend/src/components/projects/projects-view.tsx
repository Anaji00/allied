'use client';

import { useMemo } from 'react';
import { parseAsString, parseAsStringLiteral, useQueryState } from 'nuqs';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, RefreshCw } from 'lucide-react';
import { DataTable, columnHelper, recordKey } from '@/components/data/data-table';
import { DetailPanel } from '@/components/data/detail-panel';
import { FilterChips } from '@/components/data/filters';
import { EmptyState, ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Kbd } from '@/components/ui/kbd';
import { useAuth } from '@/context/AuthContext';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useListNav } from '@/hooks/use-list-nav';
import { api, ok } from '@/lib/api/client';
import { day, person, plural, since } from '@/lib/format';
import { cn } from '@/lib/utils';
import { JobDetail, ProjectDetail } from './project-detail';
import {
  hasEmail,
  isBehind,
  isGroup,
  milestoneState,
  nextMilestone,
  syncText,
  useLeadership,
  useProjects,
  waitingForMe,
  type Milestone,
  type Project,
} from './use-projects';

// `done` is only offered when the page lists jobs (a board laid out by group).
const VIEWS = ['behind', 'all', 'done', 'review', 'mine'] as const;
type View = (typeof VIEWS)[number];
type MilestoneRow = { m: Milestone; p: Project };

const pcol = columnHelper<Project>();
const mcol = columnHelper<MilestoneRow>();

export function ProjectsView() {
  const { hasRole, employeeEmail } = useAuth();
  const me = (employeeEmail ?? '').trim().toLowerCase();
  const canOperate = hasRole('OPERATOR');
  const wide = useMediaQuery('(min-width: 1280px)');
  const [view, setView] = useQueryState('show', parseAsStringLiteral(VIEWS));
  const [projectId, setProjectId] = useQueryState('project', parseAsString);
  const [milestoneId, setMilestoneId] = useQueryState('milestone', parseAsString);

  const { data, isPending, error, refetch } = useProjects();
  const { data: leadership = [] } = useLeadership();
  const client = useQueryClient();
  const sync = useMutation({
    mutationFn: async () =>
      (await ok(api.POST('/api/v1/projects/sync'))) as { skipped?: string; projects?: number; milestones?: number; removed?: number },
    onSuccess: (res) => {
      if (res.skipped) toast.warning('monday wasn’t read', { description: res.skipped });
      else
        toast.success(`Read ${plural(res.projects ?? 0, 'project')} and ${plural(res.milestones ?? 0, 'milestone')} from monday`, {
          description: res.removed ? `Removed ${res.removed} no longer on the board.` : undefined,
        });
      void client.invalidateQueries({ queryKey: ['projects'] });
    },
    onError: (e) => toast.error('Couldn’t read monday', { description: e.message }),
  });

  const projects = useMemo(() => data?.projects ?? [], [data]);
  // When every "project" is only a monday group, the jobs are what people track: one row each.
  const byJob = projects.length > 0 && projects.every(isGroup);
  const groups = projects.length;
  const lists = useMemo(() => {
    const milestones = (keep: (m: Milestone, p: Project) => boolean) =>
      projects
        .flatMap((p) => p.milestones.filter((m) => keep(m, p)).map((m) => ({ m, p })))
        // Jobs in date order, undated last; a project's milestones keep the board's order.
        .sort((a, b) => (byJob ? (a.m.due_date ?? '9999').localeCompare(b.m.due_date ?? '9999') : 0));
    return {
      behind: byJob ? milestones((m) => m.state === 'overdue') : projects.filter(isBehind),
      all: byJob ? milestones((m) => m.state !== 'done') : projects,
      done: byJob ? milestones((m) => m.state === 'done') : [],
      review: milestones((m, p) => waitingForMe(m, p, me, leadership)),
      mine: milestones((m) => hasEmail(m.owner_emails, me) && m.state !== 'done'),
    };
  }, [projects, me, leadership, byJob]);

  // An alert links to a milestone; its project is the one to open.
  const project =
    projects.find((p) => p.item_id === projectId) ??
    (milestoneId ? projects.find((p) => p.milestones.some((m) => m.item_id === milestoneId)) : undefined);
  const linkedMissing = Boolean(data && (projectId || milestoneId) && !project);

  const fallback: View = lists.behind.length || !data ? 'behind' : 'all';
  const current: View =
    view === 'done' && !byJob ? 'all' : (view ?? (project && fallback === 'behind' && !isBehind(project) ? 'all' : fallback));
  const byMilestone = byJob || current === 'review' || current === 'mine';

  const projectRows = byMilestone ? [] : (lists[current as 'behind' | 'all'] as Project[]);
  const milestoneRows = byMilestone ? (lists[current] as MilestoneRow[]) : [];
  // The open job, found in any view (an alert can link to one that isn't in the current list).
  const jobProject = byJob && milestoneId ? projects.find((p) => p.milestones.some((m) => m.item_id === milestoneId)) : undefined;
  const job = jobProject ? { p: jobProject, m: jobProject.milestones.find((m) => m.item_id === milestoneId)! } : undefined;

  const openProject = (p?: Project) => {
    void setProjectId(p ? p.item_id : null);
    void setMilestoneId(null);
  };
  const openMilestone = (r?: MilestoneRow) => {
    void setProjectId(r ? r.p.item_id : null);
    void setMilestoneId(r ? r.m.item_id : null);
  };

  const projectIndex = projectRows.findIndex((p) => p.item_id === project?.item_id);
  const milestoneIndex = milestoneRows.findIndex((r) => r.m.item_id === milestoneId);
  useListNav<Project | MilestoneRow>(byMilestone ? milestoneRows : projectRows, byMilestone ? milestoneIndex : projectIndex, (row) =>
    byMilestone ? openMilestone(row as MilestoneRow | undefined) : openProject(row as Project | undefined),
  );

  const projectColumns = pcol.columns([
    pcol.accessor('name', { header: 'Project', meta: { className: 'max-w-72 truncate font-medium' } }),
    pcol.display({
      id: 'progress',
      header: 'Milestones',
      cell: (c) => {
        const ms = c.row.original.milestones;
        const done = ms.filter((m) => m.state === 'done').length;
        return ms.length ? <Progress done={done} total={ms.length} /> : <span className="text-muted-foreground">None yet</span>;
      },
    }),
    pcol.display({
      id: 'next',
      header: 'Waiting on',
      meta: { className: 'max-w-64 truncate' },
      cell: (c) => {
        const m = nextMilestone(c.row.original);
        return m ? m.name : <span className="text-muted-foreground">Nothing</span>;
      },
    }),
    pcol.accessor('due_date', { header: 'Due', cell: (c) => day(c.getValue()) }),
    pcol.accessor('owner_emails', { header: 'Owner', cell: (c) => (c.getValue() ?? []).map((e) => person(e, me)).join(', ') || '–' }),
    pcol.display({
      id: 'state',
      header: 'State',
      cell: (c) => {
        const p = c.row.original;
        if (p.done) return <ToneBadge tone="ok">Done</ToneBadge>;
        if (p.behind) return <ToneBadge tone="bad">{plural(p.behind, 'milestone')} late</ToneBadge>;
        if (p.overdue) return <ToneBadge tone="bad">Past due</ToneBadge>;
        const missing = p.milestones.filter((m) => m.missing_docs).length;
        return missing ? <ToneBadge tone="warn">Design docs needed</ToneBadge> : <ToneBadge>On track</ToneBadge>;
      },
    }),
  ]);

  const milestoneColumns = mcol.columns([
    mcol.display({
      id: 'milestone',
      header: byJob ? 'Job' : 'Milestone',
      meta: { className: 'max-w-72 truncate font-medium' },
      cell: (c) => c.row.original.m.name,
    }),
    ...(byJob
      ? [
          mcol.display({ id: 'status', header: 'monday status', cell: (c) => c.row.original.m.status || '–' }),
          mcol.display({
            id: 'owner',
            header: 'Owner',
            meta: { className: 'max-w-56 truncate' },
            cell: (c) => (c.row.original.m.owner_emails ?? []).map((e) => person(e, me)).join(', ') || '–',
          }),
          ...(groups > 1
            ? [mcol.display({ id: 'group', header: 'Group', meta: { className: 'max-w-48 truncate' }, cell: (c) => c.row.original.p.name })]
            : []),
        ]
      : [mcol.display({ id: 'project', header: 'Project', meta: { className: 'max-w-64 truncate' }, cell: (c) => c.row.original.p.name })]),
    mcol.display({ id: 'due', header: 'Due', cell: (c) => day(c.row.original.m.due_date) }),
    mcol.display({
      id: 'state',
      header: 'State',
      cell: (c) => {
        const s = milestoneState(c.row.original.m);
        return <ToneBadge tone={s.tone}>{s.label}</ToneBadge>;
      },
    }),
    mcol.display({
      id: 'version',
      header: 'Latest version',
      cell: (c) => {
        const sub = c.row.original.m.latest_submission;
        return sub ? `Version ${sub.version}, sent by ${person(sub.submitted_by, me)} ${since(sub.submitted_at)}` : '–';
      },
    }),
  ]);

  const stale = syncText(data?.last_sync);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChips
          label="Show"
          value={current}
          onChange={(v) => {
            void setView(v);
            openProject(undefined);
          }}
          options={[
            { value: 'behind', label: 'Behind', count: data ? lists.behind.length : undefined, tone: 'bad' },
            { value: 'all', label: byJob ? 'Open jobs' : 'All projects', count: data ? lists.all.length : undefined },
            ...(byJob ? [{ value: 'done' as const, label: 'Finished', count: data ? lists.done.length : undefined }] : []),
            { value: 'review', label: 'Waiting for my review', count: data ? lists.review.length : undefined, tone: 'warn' },
            { value: 'mine', label: byJob ? 'My jobs' : 'My milestones', count: data ? lists.mine.length : undefined },
          ]}
        />
        <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          <span className={cn(stale && data?.last_sync?.error && 'text-bad')}>
            {stale ?? `Read from monday ${since(data?.last_sync?.at)}`}
          </span>
          {canOperate && (
            <Button variant="outline" size="sm" onClick={() => sync.mutate()} disabled={sync.isPending}>
              {sync.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Refresh from monday
            </Button>
          )}
        </div>
      </div>

      {linkedMissing && (
        <p role="status" className="text-sm text-warn">
          That {milestoneId ? 'milestone' : 'project'} is no longer on the monday projects board.
        </p>
      )}

      <div className={cn('grid items-start gap-4', wide && project && 'grid-cols-[minmax(0,1fr)_28rem]')}>
        <div className="grid min-w-0 gap-2">
          {isPending ? (
            <RowsSkeleton rows={8} />
          ) : error ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : projects.length === 0 ? (
            <EmptyState title="No projects yet">
              Projects and their milestones come from the monday projects board. It’s read every 10 minutes, so new projects show up here
              shortly after they’re added on monday.
            </EmptyState>
          ) : byMilestone ? (
            <DataTable
              label={byJob ? 'Jobs' : current === 'review' ? 'Milestones waiting for my review' : 'My milestones'}
              columns={milestoneColumns}
              data={milestoneRows}
              getRowId={(r) => r.m.item_id}
              selectedId={milestoneId}
              onSelect={openMilestone}
              minWidth={760}
              empty={<EmptyState title={EMPTY[current][0]}>{EMPTY[current][1]}</EmptyState>}
            />
          ) : (
            <DataTable
              label="Projects"
              columns={projectColumns}
              data={projectRows}
              getRowId={(p) => p.item_id}
              recordName={(p) => recordKey('project', p.item_id)}
              selectedId={project?.item_id}
              onSelect={openProject}
              minWidth={820}
              empty={<EmptyState title="Nothing is behind schedule">Projects with a late milestone show up here.</EmptyState>}
            />
          )}
          {projects.length > 0 && (
            <p className="hidden text-xs text-muted-foreground md:block">
              <Kbd>J</Kbd>
              <Kbd>K</Kbd> move · <Kbd>Esc</Kbd> close
            </p>
          )}
        </div>

        <DetailPanel
          open={byJob ? Boolean(job) : Boolean(project)}
          onClose={() => openProject(undefined)}
          recordName={project && !byMilestone ? recordKey('project', project.item_id) : undefined}
          title={(byJob ? job?.m.name : project?.name) ?? ''}
          description={(byJob ? job?.p.group_title : project?.group_title) || undefined}
        >
          {byJob
            ? job && <JobDetail key={job.m.item_id} milestone={job.m} project={job.p} leadership={leadership} />
            : project && (
                <ProjectDetail
                  key={project.item_id}
                  project={project}
                  leadership={leadership}
                  openMilestone={milestoneId}
                  onToggleMilestone={(id) => void setMilestoneId(id === milestoneId ? null : id)}
                />
              )}
        </DetailPanel>
      </div>
    </div>
  );
}

const EMPTY: Record<View, [string, string]> = {
  behind: ['Nothing is behind schedule', 'Jobs past their due date show up here.'],
  all: ['No open jobs', 'Jobs on the monday projects board that aren’t finished show up here.'],
  done: ['Nothing finished yet', 'Jobs marked finished on monday show up here.'],
  review: ['Nothing is waiting for your review', 'Design documents you’re asked to review show up here.'],
  mine: ['Nothing open is yours', 'Jobs you own on the monday projects board show up here.'],
};

function Progress({ done, total }: { done: number; total: number }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span className="block h-full rounded-full bg-ok" style={{ width: `${(done / total) * 100}%` }} />
      </span>
      <span className="text-xs text-muted-foreground tabular-nums">
        {done} of {total}
      </span>
    </span>
  );
}
