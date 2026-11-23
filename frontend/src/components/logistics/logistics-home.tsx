'use client';

import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { CarriersPage } from '@/components/admin/carriers-page';
import { SectionTabs } from '@/components/data/filters';
import { PackingSlips } from '@/components/orders/packing-slips';
import { ForwarderDigest } from '@/components/shipments/forwarder-digest';
import { ShipmentsView } from '@/components/shipments/shipments-view';
import { useAuth } from '@/context/AuthContext';

const SECTIONS = ['shipments', 'slips', 'carriers'] as const;
const LABEL = {
  shipments: 'Shipments',
  slips: 'Packing slips',
  carriers: 'Carrier prices',
};

/** Freight and what goes with it: shipments, packing slips, carrier prices, and the certificates that ship with the steel. */
export function LogisticsHome() {
  const { hasRole } = useAuth();
  const [section, setSection] = useQueryState('section', parseAsStringLiteral(SECTIONS).withDefault('shipments'));
  const shown = SECTIONS.filter((s) => s !== 'carriers' || hasRole('OPERATOR'));
  return (
    <div className="grid min-w-0 gap-4">
      <SectionTabs
        label="Freight and shipping sections"
        value={section}
        onChange={(s) => void setSection(s === 'shipments' ? null : s)}
        tabs={shown.map((s) => [s, LABEL[s]] as const)}
      />
      {section === 'shipments' && <ForwarderDigest />}
      {section === 'shipments' && <ShipmentsView />}
      {section === 'slips' && <PackingSlips />}
      {section === 'carriers' && hasRole('OPERATOR') && <CarriersPage />}
    </div>
  );
}
