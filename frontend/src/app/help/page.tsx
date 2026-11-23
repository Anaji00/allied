import { HelpHome } from '@/components/help/help-home';
import { SecretaryTabs } from '@/components/shell/secretary-tabs';

export const metadata = { title: 'Help' };

export default function Page() {
  return (
    <>
      <SecretaryTabs />
      <HelpHome />
    </>
  );
}
