import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  BadgeCheck,
  ClipboardList,
  Database,
  Factory,
  FolderKanban,
  Gauge,
  Inbox,
  ReceiptText,
  ScrollText,
  Ship,
  Sparkles,
  TrendingUp,
  Users,
} from 'lucide-react';

export type Role = 'ADMIN' | 'MANAGER' | 'OPERATOR' | 'VIEWER';

export type NavItem = {
  href: string;
  label: string;
  description: string;
  icon: LucideIcon;
  /** Hides the item from anyone the API would refuse anyway. */
  minRole?: Role;
};

export type NavGroup = { section: string; items: NavItem[] };

// One list drives the sidebar, the page header and the command menu, so a
// page's name and description cannot drift between them.
export const NAV: NavGroup[] = [
  {
    section: 'Daily work',
    items: [
      { href: '/', label: 'Inbox', description: 'Everything waiting on you: approvals, problems and schedule warnings', icon: Inbox },
      { href: '/orders', label: 'Orders', description: 'Every order with its lines, margin, mill certs and Sage status; emails, bills and suppliers', icon: ReceiptText },
      { href: '/projects', label: 'Projects', description: 'Milestones from monday, design documents to submit and review, and what is behind', icon: FolderKanban },
      { href: '/shipments', label: 'Shipments', description: 'Freight on the way: on time, slipping, or waiting on the forwarder', icon: Ship },
      { href: '/vendors', label: 'Vendors', description: 'Vendor POs against project dates, what to chase, vendor on-time scores', icon: Factory },
      { href: '/rfqs', label: 'RFQs', description: 'Customer quote requests, deadlines and owners', icon: ClipboardList },
      { href: '/chat', label: 'Assistant', description: 'Ask about specs and past orders; read documents, translate, draft email, check drawings', icon: Sparkles },
    ],
  },
  {
    section: 'Sales',
    items: [
      { href: '/sales/pipeline', label: 'Wear & quotes', description: 'Equipment wear tracking and replacement quotes', icon: Gauge },
      { href: '/sales/leads', label: 'Leads', description: 'Prospects, outreach and cost-of-ownership calculator', icon: TrendingUp },
    ],
  },
  {
    section: 'Quality',
    items: [
      { href: '/compliance/vault', label: 'Mill certificates', description: 'Heat numbers, ASTM limits and quarantines', icon: BadgeCheck },
    ],
  },
  {
    section: 'Admin',
    items: [
      { href: '/alerts', label: 'People & alerts', description: 'Alert history, the daily summary, who gets told, and user accounts', icon: Users },
      { href: '/health', label: 'System', description: 'Health, what runs by itself, background workers and the monday.com link', icon: Activity },
      { href: '/operations', label: 'Sage & imports', description: 'Sage 50 import files and customer, product and supplier list imports', icon: Database, minRole: 'MANAGER' },
      { href: '/admin/audit', label: 'Audit trail', description: 'Who did what, and when', icon: ScrollText, minRole: 'MANAGER' },
    ],
  },
];

const FLAT = NAV.flatMap((group) => group.items);

/** The phone tab bar; every other page sits behind its "More" button. */
export const PHONE_TABS: NavItem[] = ['/', '/orders', '/projects', '/shipments'].map(
  (href) => FLAT.find((item) => item.href === href)!,
);

export function isActive(href: string, pathname: string) {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function pageFor(pathname: string): Pick<NavItem, 'label' | 'description'> {
  return FLAT.find((item) => isActive(item.href, pathname)) || { label: 'Allied Operations', description: '' };
}

/** The groups and items this role may open; a group left empty is dropped. */
export function navFor(hasRole: (role: Role) => boolean): NavGroup[] {
  return NAV.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.minRole || hasRole(item.minRole)),
  })).filter((group) => group.items.length > 0);
}
