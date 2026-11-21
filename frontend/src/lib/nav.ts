import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  BadgeCheck,
  ClipboardList,
  Database,
  Factory,
  FolderKanban,
  Inbox,
  LayoutGrid,
  Mail,
  MessagesSquare,
  Wrench,
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
  /** Folded into another page: kept for page titles and old links, left out of the menu. */
  hidden?: boolean;
};

export type NavGroup = { section: string; items: NavItem[] };

// One list drives the sidebar, the page header and the command menu, so a
// page's name and description cannot drift between them.
export const NAV: NavGroup[] = [
  {
    section: 'Secretary',
    items: [
      {
        hidden: true,
        href: '/inbox',
        label: 'Today',
        description: 'Everything waiting on you: approvals and problems, your day, and who has the ball',
        icon: Inbox,
      },
      {
        hidden: true,
        href: '/secretary/mail',
        label: 'Email',
        description: 'Mail that needs a reply or is waiting on a vendor, with a reply drafted for you to check. Nothing is sent from here',
        icon: Mail,
      },
      {
        hidden: true,
        href: '/orders',
        label: 'Orders & Sage',
        description: 'Quote requests, sales orders and purchase orders with margin and mill certs, and what goes to and comes from Sage',
        icon: ReceiptText,
      },
      {
        hidden: true,
        href: '/contacts',
        label: 'Contacts',
        description: 'Customers, vendors with their on-time record, suppliers, and carriers’ freight prices',
        icon: Factory,
      },
    ],
  },
  {
    section: 'Boards',
    items: [
      {
        hidden: true,
        href: '/jobs',
        label: 'Jobs',
        description: 'Each job from the customer’s request to the shipment, as a board next to your monday boards',
        icon: Route,
      },
      {
        hidden: true,
        href: '/boards',
        label: 'Boards',
        description: 'Your monday boards: edit cells, add rows, comment, and see what is late. Changes wait for a manager to send them',
        icon: LayoutGrid,
      },
    ],
  },
  {
    section: 'Toolkit',
    items: [
      {
        hidden: true,
        href: '/toolkit',
        label: 'Toolkit',
        description: 'Read documents, translate, draft email, check and compare drawings, look up an order, export a board',
        icon: Wrench,
      },
      {
        hidden: true,
        href: '/conversations',
        label: 'Conversations',
        description: 'Comments live on each job and order',
        icon: MessagesSquare,
      },
    ],
  },
  {
    section: 'More',
    items: [
      {
        href: '/projects',
        hidden: true,
        label: 'Projects',
        description: 'Milestones from monday, design documents to submit and review, and what is behind',
        icon: FolderKanban,
      },
      {
        href: '/shipments',
        hidden: true,
        label: 'Shipments',
        description: 'Freight on the way: on time, slipping, or waiting on the forwarder',
        icon: Ship,
      },
      {
        href: '/vendors',
        hidden: true,
        label: 'Vendors',
        description: 'Vendor POs against project dates, what to chase, vendor on-time scores',
        icon: Factory,
      },
      {
        href: '/carriers',
        hidden: true,
        label: 'Carrier prices',
        description: 'Your own freight prices, when each was last checked, and live carrier prices',
        icon: Truck,
        minRole: 'OPERATOR',
      },
      {
        hidden: true,
        href: '/sales',
        label: 'Sales',
        description: 'Prospects and outreach, and equipment wear with replacement quotes',
        icon: TrendingUp,
        minRole: 'OPERATOR',
      },
      {
        href: '/alerts',
        hidden: true,
        label: 'People & alerts',
        description: 'Alert history, the daily summary, who gets told, and user accounts',
        icon: Users,
      },
      {
        hidden: true,
        href: '/admin',
        label: 'Admin',
        description: 'Health, what runs by itself, background workers and the monday.com link',
        icon: Activity,
      },
      {
        href: '/operations',
        hidden: true,
        label: 'Sage & imports',
        description: 'Sage 50 import files and customer, product and supplier list imports',
        icon: Database,
        minRole: 'MANAGER',
      },
      {
        href: '/rfqs',
        hidden: true,
        label: 'RFQs',
        description: 'Customer quote requests, deadlines and owners',
        icon: ClipboardList,
      },
      {
        href: '/admin/audit',
        hidden: true,
        label: 'Audit trail',
        description: 'Who did what, and when',
        icon: ScrollText,
        minRole: 'MANAGER',
      },
    ],
  },
  {
    section: 'Allied',
    items: [
      {
        href: '/secretary',
        label: 'Secretary',
        description: 'The day’s work: what is waiting on you, email and drafts, orders and Sage, contacts and sales',
        icon: Inbox,
      },
      {
        href: '/boards',
        label: 'Boards',
        description: 'Jobs are saved in the app. monday boards are copies that only change when a manager sends the changes',
        icon: LayoutGrid,
      },
      {
        href: '/toolkit',
        label: 'Toolkit',
        description: 'Read documents, translate, draft email, check and compare drawings, look up an order, export a board',
        icon: Wrench,
      },
      {
        href: '/admin',
        label: 'Admin',
        description: 'Freight, certificates, people and alerts, Sage files, automations, health and the audit trail',
        icon: Activity,
      },
    ],
  },
];

const FLAT = NAV.flatMap((group) => group.items);

/** The phone tab bar; every other page sits behind its "More" button. */
export const PHONE_TABS: NavItem[] = ['/secretary', '/boards', '/toolkit', '/admin'].map((href) =>
  FLAT.find((item) => item.href === href)!,
);

// Pages that live under one menu item, reached by the tabs at the top of each.
const TABBED: Record<string, string[]> = {
  '/secretary': [
    '/inbox',
    '/secretary/mail',
    '/orders',
    '/contacts',
    '/sales',
    '/chase',
    '/mine',
    '/ball',
    '/conversations',
    '/rfqs',
    '/vendors',
  ],
  '/admin': ['/health', '/alerts', '/operations', '/admin/audit', '/logistics', '/shipments', '/carriers', '/compliance'],
  '/sales': ['/sales/leads', '/sales/pipeline'],
  '/boards': ['/jobs', '/projects'],
  '/toolkit': ['/chat'],
  '/contacts': ['/vendors'],
  '/orders': ['/rfqs'],
};

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
    items: group.items.filter((item) => !item.hidden && (!item.minRole || hasRole(item.minRole))),
  })).filter((group) => group.items.length > 0);
}
