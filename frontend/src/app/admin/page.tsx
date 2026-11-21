import { AdminTabs } from '@/components/shell/admin-tabs';
import { AdminHome } from '@/components/admin/admin-home';

export const metadata = { title: 'Admin' };

export default function Page() {
  return (
    <>
      <AdminTabs />
      <AdminHome />
    </>
  );
}
