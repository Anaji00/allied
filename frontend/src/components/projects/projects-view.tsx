'use client';

import { useMemo } from 'react';
import { parseAsString, parseAsStringLiteral, useQueryState } from 'nuqs';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2, RefreshCw } from 'lucide-react';
import { DataTable, columnHelper } from '@/components/data/data-table';
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
import { ProjectDetail } from './project-detail';
import {
  hasEmail,
  isBehind,
  milestoneState,
  nextMilestone,
  syncText,
  useLeadership,
  useProjects,
  waitingForMe,
  type Milestone,
  type Project,
} from './use-projects';

const VIEWS = ['behind', 'all', 'review', 'mine'] as const;
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
  const lists = useMemo(() => {
    const milestones = (keep: (m: Milestone, p: Project) => boolean) =>
      projects.flatMap((p) => p.milestones.filter((m) => keep(m, p)).map((m) => ({ m, p })));
    return {
      behind: projects.filter(isBehind),
      all: projects,
      review: milestones((m, p) => waitingForMe(m, p, me, leadership)),
      mine: milestones((m) => hasEmail(m.owner_emails, me) && m.state !== 'done'),
    };
  }, [projects, me, leadership]);

  // An alert links to a milestone; its project is the one to open.
  const project =
    projects.find((p) => p.item_id === projectId) ??
    (milestoneId ? projects.find((p) => p.milestones.some((m) => m.item_id === milestoneId)) : undefined);
  const linkedMissing = Boolean(data && (projectId || milestoneId) && !project);

  const fallback: View = lists.behind.length || !data ? 'behind' : 'all';
  const current: View = view ?? (project && fallback === 'behind' && !isBehind(project) ? 'all' : fallback);
  const byMilestone = current === 'review' || current === 'mine';

  const projectRows = byMilestone ? [] : lists[current as 'behind' | 'all'];
  const milestoneRows = byMilestone ? lists[current as 'review' | 'mine'] : [];

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
  useListNav<Project | MilestoneRow>(
    byMilestone ? milestoneRows : projectRows,
    byMilestone ? milestoneIndex : projectIndex,
    (row) => (byMilestone ? openMilestone(row as MilestoneRow | undefined) : openProject(row as Project | undefined)),
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
    mcol.display({ id: 'milestone', header: 'Milestone', meta: { className: 'max-w-72 truncate font-medium' }, cell: (c) => c.row.original.m.name }),
    mcol.display({ id: 'project', header: 'Project', meta: { className: 'max-w-64 truncate' }, cell: (c) => c.row.original.p.name }),
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
            { value: 'all', label: 'All projects', count: data ? lists.all.length : undefined },
            { value: 'review', label: 'Waiting for my review', count: data ? lists.review.length : undefined, tone: 'warn' },
            { value: 'mine', label: 'My milestones', count: data ? lists.mine.length : undefined },
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
              Projects and their milestones come from the monday projects board. It’s read every 10 minutes, so new projects show
              up here shortly after they’re added on monday.
            </EmptyState>
          ) : byMilestone ? (
            <DataTable
              label={current === 'review' ? 'Milestones waiting for my review' : 'My milestones'}
              columns={milestoneColumns}
              data={milestoneRows}
              getRowId={(r) => r.m.item_id}
              selectedId={milestoneId}
              onSelect={openMilestone}
              minWidth={760}
              empty={
                <EmptyState title={current === 'review' ? 'Nothing is waiting for your review' : 'You have no open milestones'}>
                  {current === 'review'
                    ? 'Design documents you’re asked to review show up here.'
                    : 'Milestones you own on the monday projects board show up here.'}
                </EmptyState>
              }
            />
          ) : (
            <DataTable
              label="Projects"
              columns={projectColumns}
              data={projectRows}
              getRowId={(p) => p.item_id}
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
          open={Boolean(project)}
          onClose={() => openProject(undefined)}
          title={project?.name ?? ''}
          description={project?.group_title || undefined}
        >
          {project && (
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
