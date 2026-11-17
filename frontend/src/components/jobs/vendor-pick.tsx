'use client';

import { SuggestInput } from '@/components/data/pick';
import { useSuppliers } from '@/components/orders/use-orders';

/** A vendor name box that suggests the suppliers the app already knows, and still lets you type a new one. */
export function VendorInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const suppliers = useSuppliers();
  return <SuggestInput value={value} onChange={onChange} options={(suppliers.data ?? []).map((s) => s.name)} />;
}
