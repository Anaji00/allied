// One list drives the sidebar and the page header, so a page's name and
// description cannot drift between the two (the header used to keep its own
// if/else chain of titles).
export const NAV = [
  {
    section: 'Daily work',
    items: [
      { href: '/', label: 'Overview', description: 'Orders, margins and what needs attention today' },
      { href: '/projects', label: 'Projects', description: 'Milestones from monday, design documents to submit and review, and what is behind' },
      { href: '/shipments', label: 'Shipments', description: 'Freight on the way: on time, slipping, or waiting on the forwarder' },
      { href: '/alerts', label: 'Alerts', description: 'Everything that needs you, in one place; who gets told and how' },
      { href: '/operations', label: 'Orders & Sage', description: 'Sales orders, vendor POs, bill matching and Sage import files' },
      { href: '/documents', label: 'Documents', description: 'Read customer POs, drawings and mill certificates' },
    ],
  },
  {
    section: 'Automation',
    items: [
      { href: '/ai', label: 'AI tools', description: 'Translate, read email, check drawings, summarise calls; a person approves anything binding' },
      { href: '/integrations', label: 'monday.com link', description: 'Board events received, what they did, and anything stuck' },
      { href: '/automation', label: 'What runs by itself', description: 'Which steps are automatic and what still needs a person' },
      { href: '/health', label: 'System health', description: 'Service, database and connection status' },
    ],
  },
  {
    section: 'Sales',
    items: [
      { href: '/sales/pipeline', label: 'Wear & quotes', description: 'Equipment wear tracking and replacement quotes' },
      { href: '/sales/leads', label: 'Leads', description: 'Prospects, outreach and cost-of-ownership calculator' },
    ],
  },
  {
    section: 'Quality & records',
    items: [
      { href: '/compliance/vault', label: 'Mill certificates', description: 'Heat numbers, ASTM limits and quarantines' },
      { href: '/admin/audit', label: 'Audit trail', description: 'Who did what, and when' },
      { href: '/chat', label: 'Ask the assistant', description: 'Search specs, procedures and past orders' },
    ],
  },
];

const FLAT = NAV.flatMap((group) => group.items);

export function isActive(href, pathname) {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function pageFor(pathname) {
  return FLAT.find((item) => isActive(item.href, pathname)) || { label: 'Allied Operations', description: '' };
}
