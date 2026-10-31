import { Suspense } from 'react';
import { RowsSkeleton } from '@/components/data/states';
import { OrdersView } from '@/components/orders/orders-view';

export const metadata = { title: 'Orders' };

export default function OrdersPage() {
  return (
    <Suspense fallback={<RowsSkeleton rows={8} />}>
      <OrdersView />
    </Suspense>
  );
}
