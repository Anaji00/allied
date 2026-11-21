import { SecretaryTabs } from '@/components/shell/secretary-tabs';
import { Suspense } from 'react';
import { ContactsHome } from '@/components/contacts/contacts-home';
import { RowsSkeleton } from '@/components/data/states';

export const metadata = { title: 'Contacts' };

export default function Page() {
  return (
    <>
      <SecretaryTabs />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <ContactsHome />
      </Suspense>
    </>
  );
}
