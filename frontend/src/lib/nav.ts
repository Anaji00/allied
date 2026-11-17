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
  LayoutGrid,
  ReceiptText,
  ScrollText,
  Route,
  Ship,
  TrendingUp,
  Truck,
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
      {
        href: '/',
        label: 'Boards',
        description: 'Each monday board as monday shows it, with the automations that act on it',
        icon: LayoutGrid,
      },
      {
        href: '/jobs',
        label: 'Jobs',
        description: 'Each job from the customer’s request to the shipment: what is done, what is waiting, and on whom',
        icon: Route,
      },
      {
        href: '/inbox',
        label: 'Today',
        description: 'Everything waiting on you: approvals and problems, your day, and who has the ball',
        icon: Inbox,
      },
      {
        href: '/orders',
        label: 'Orders',
        description: 'Every order with its lines, margin, mill certs and Sage status; emails, bills and suppliers',
        icon: ReceiptText,
      },
      {
        href: '/projects',
        label: 'Projects',
        description: 'Milestones from monday, design documents to submit and review, and what is behind',
        icon: FolderKanban,
      },
      {
        href: '/shipments',
        label: 'Shipments',
        description: 'Freight on the way: on time, slipping, or waiting on the forwarder',
        icon: Ship,
      },
      {
        href: '/vendors',
        label: 'Vendors',
        description: 'Vendor POs against project dates, what to chase, vendor on-time scores',
        icon: Factory,
      },
      {
        href: '/carriers',
        label: 'Carrier prices',
        description: 'Your own freight prices, when each was last checked, and live carrier prices',
        icon: Truck,
        minRole: 'OPERATOR',
      },
      { href: '/rfqs', label: 'RFQs', description: 'Customer quote requests, deadlines and owners', icon: ClipboardList },
    ],
  },
  {
    section: 'Sales & quality',
    items: [
      {
        href: '/sales/pipeline',
        label: 'Wear & quotes',
        description: 'Equipment wear tracking and replacement quotes',
        icon: Gauge,
        minRole: 'OPERATOR',
      },
      {
        href: '/sales/leads',
        label: 'Leads',
        description: 'Prospects, outreach and cost-of-ownership calculator',
        icon: TrendingUp,
        minRole: 'OPERATOR',
      },
      { href: '/compliance/vault', label: 'Mill certificates', description: 'Heat numbers, ASTM limits and quarantines', icon: BadgeCheck },
    ],
  },
  {
    section: 'Admin',
    items: [
      {
        href: '/alerts',
        label: 'People & alerts',
        description: 'Alert history, the daily summary, who gets told, and user accounts',
        icon: Users,
      },
      {
        href: '/health',
        label: 'System',
        description: 'Health, what runs by itself, background workers and the monday.com link',
        icon: Activity,
      },
      {
        href: '/operations',
        label: 'Sage & imports',
        description: 'Sage 50 import files and customer, product and supplier list imports',
        icon: Database,
        minRole: 'MANAGER',
      },
      { href: '/admin/audit', label: 'Audit trail', description: 'Who did what, and when', icon: ScrollText, minRole: 'MANAGER' },
    ],
  },
];

const FLAT = NAV.flatMap((group) => group.items);

/** The phone tab bar; every other page sits behind its "More" button. */
export const PHONE_TABS: NavItem[] = ['/', '/inbox', '/orders', '/projects'].map((href) => FLAT.find((item) => item.href === href)!);

// Pages that live under one menu item, reached by the tabs at the top of each.
const TABBED: Record<string, string[]> = { '/inbox': ['/mine', '/ball'] };

export function isActive(href: string, pathname: string) {
  if (href === '/') return pathname === '/';
  if (TABBED[href]?.some((h) => pathname === h || pathname.startsWith(`${h}/`))) return true;
  return pathname === href || pathname.startsWith(`${href}/`);
}

// Not in the sidebar: reached from the assistant panel's "Other tools" link.
const TOOLS_PAGE = {
  href: '/chat',
  label: 'Document tools',
  description: 'Read a document, translate, draft email, check drawings and more',
};

export function pageFor(pathname: string): Pick<NavItem, 'label' | 'description' | 'href'> {
  if (isActive(TOOLS_PAGE.href, pathname)) return TOOLS_PAGE;
  return FLAT.find((item) => isActive(item.href, pathname)) || { href: '/', label: 'Allied Operations', description: '' };
}

/** The groups and items this role may open; a group left empty is dropped. */
export function navFor(hasRole: (role: Role) => boolean): NavGroup[] {
  return NAV.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.minRole || hasRole(item.minRole)),
  })).filter((group) => group.items.length > 0);
}
