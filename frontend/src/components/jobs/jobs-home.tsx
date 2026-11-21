'use client';

import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { TabsWithMore } from '@/components/data/tabs-more';
import { ProjectsView } from '@/components/projects/projects-view';
import { ShipmentsView } from '@/components/shipments/shipments-view';
import { JobsView } from './jobs-view';

const SECTIONS = ['jobs', 'projects', 'shipments'] as const;
const LABEL: Record<(typeof SECTIONS)[number], string> = {
  jobs: 'Jobs',
  projects: 'Projects',
  shipments: 'Freight',
};

/** Jobs, with the projects, freight, vendors and carrier prices that belong to a job folded in as sections, so there is one place to work from. */
export function JobsHome() {
  const [section, setSection] = useQueryState('section', parseAsStringLiteral(SECTIONS).withDefault('jobs'));
  const sections = SECTIONS;
  return (
    <div className="grid min-w-0 gap-4">
      <TabsWithMore
        label="Job sections"
        show={3}
        value={section}
        onChange={(s) => void setSection(s === 'jobs' ? null : s)}
        tabs={sections.map((s) => [s, LABEL[s]] as const)}
      />
      {section === 'jobs' && <JobsView />}
      {section === 'projects' && <ProjectsView />}
      {section === 'shipments' && <ShipmentsView />}
    </div>
  );
}
