import { SecretaryTabs } from '@/components/shell/secretary-tabs';
import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { MailQueue } from '@/components/secretary/mail-queue';
import { MailSettingsWhenConnected, MailStatusBanner } from '@/components/secretary/mail-status';

export const metadata = { title: 'Mail' };

export default function Page() {
  return (
    <div className="grid gap-4">
      <SecretaryTabs />
      <MailStatusBanner />
      <Suspense fallback={<RowsSkeleton rows={6} />}>
        <MailQueue />
      </Suspense>
      <MailSettingsWhenConnected />
    </div>
  );
}
