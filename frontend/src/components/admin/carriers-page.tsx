'use client';

import { CarrierPrices } from './carrier-prices';
import { FedEx } from './system-view';
import { useAuth } from '@/context/AuthContext';

export function CarriersPage() {
  const { hasRole } = useAuth();
  return (
    <div className="grid gap-4">
      <CarrierPrices />
      {hasRole('MANAGER') && <FedEx />}
    </div>
  );
}
