import { AdminTabs } from '@/components/shell/admin-tabs';
import { AdminHome } from '@/components/admin/admin-home';

export const metadata = { title: 'Control room' };

export default function Page() {
  return (
    <>
      <AdminTabs />
      <AdminHome />
    </>
  );
}
