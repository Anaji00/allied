import { SecretaryTabs } from '@/components/shell/secretary-tabs';
import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { ChaseView } from '@/components/today/chase-view';
import { VendorsLate } from '@/components/today/vendors-late';
import { PaymentsDue } from '@/components/today/payments-due';
import { VendorPayments } from '@/components/today/vendor-payments';
import { CertsToChase } from '@/components/today/certs-to-chase';

export const metadata = { title: 'Who owes us' };

export default function Page() {
  return (
    <>
      <SecretaryTabs />
      <VendorsLate />
      <PaymentsDue />
      <VendorPayments />
      <CertsToChase />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <ChaseView />
      </Suspense>
    </>
  );
}
