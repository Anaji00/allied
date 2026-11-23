'use client';

import { parseAsStringLiteral, useQueryState } from 'nuqs';
import { TabsWithMore } from '@/components/data/tabs-more';
import { VendorsView } from '@/components/vendors/vendors-view';
import { CompanyNamesTool } from '@/components/organize/company-names';
import { VendorContacts } from '@/components/organize/vendor-contacts';
import { Suppliers } from '@/components/orders/order-tabs';
import { ContactsList } from './contacts-list';

const SECTIONS = ['customers', 'vendors', 'people', 'suppliers', 'companies'] as const;
const LABEL = { customers: 'Customers', vendors: 'Vendors', people: 'Vendor contacts', suppliers: 'Suppliers', companies: 'Company names' };

/** The companies Allied deals with: vendors (with their on-time record) and the carriers' prices. */
export function ContactsHome() {
  const [section, setSection] = useQueryState('section', parseAsStringLiteral(SECTIONS).withDefault('customers'));
  const sections = SECTIONS;
  return (
    <div className="grid min-w-0 gap-4">
      {sections.length > 1 && (
        <TabsWithMore
          label="Contacts sections"
          show={3}
          value={section}
          onChange={(s) => void setSection(s === 'customers' ? null : s)}
          tabs={sections.map((s) => [s, LABEL[s]] as const)}
        />
      )}
      {section === 'customers' && <ContactsList kind="customer" />}
      {section === 'vendors' && <VendorsView />}
      {section === 'people' && <VendorContacts />}
      {section === 'suppliers' && <Suppliers />}
      {section === 'companies' && <CompanyNamesTool />}
    </div>
  );
}
