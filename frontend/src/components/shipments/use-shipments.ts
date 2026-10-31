'use client';

import { useQuery } from '@tanstack/react-query';
import type { Tone } from '@/components/data/tone-badge';
import { api, ok } from '@/lib/api/client';

export type ShipmentEvent = { at: string; status: string; description?: string | null; source?: string | null; eta?: string | null };

export type Shipment = {
  id: number;
  project_item_id?: string | null;
  forwarder?: string | null;
  mode: 'SEA' | 'AIR' | 'TRUCK';
  reference?: string | null;
  container_no?: string | null;
  awb?: string | null;
  carrier?: string | null;
  origin?: string | null;
  destination?: string | null;
  etd?: string | null;
  eta_original?: string | null;
  eta_current?: string | null;
  free_time_ends?: string | null;
  status: string;
  owner_email?: string | null;
  last_update_at?: string | null;
  flags?: string[];
  events?: ShipmentEvent[];
};

export const BEFORE_DEPARTURE = ['BOOKED', 'CARGO_READY', 'GATE_IN'];
export const UNDER_WAY = ['DEPARTED', 'TRANSSHIPMENT'];
export const FINISHED = ['GATE_OUT', 'DELIVERED', 'EMPTY_RETURNED'];

const STATUS_LABEL: Record<string, string> = {
  BOOKED: 'Booked',
  CARGO_READY: 'Ready to collect',
  GATE_IN: 'At origin port',
  DEPARTED: 'On the way',
  TRANSSHIPMENT: 'Changing ship or plane',
  ARRIVED: 'Arrived',
  DISCHARGED: 'Unloaded at port',
  CUSTOMS_RELEASED: 'Cleared customs',
  GATE_OUT: 'Left the port',
  DELIVERED: 'Delivered',
  EMPTY_RETURNED: 'Finished (container returned)',
  EXCEPTION: 'Problem reported',
  UNKNOWN: 'Unclear',
};
const DEPARTED_BY_MODE: Record<string, string> = { SEA: 'On the water', AIR: 'In the air', TRUCK: 'On the road' };

export const MODE_LABEL: Record<string, string> = { SEA: 'Sea', AIR: 'Air', TRUCK: 'Truck' };
export const SOURCE_LABEL: Record<string, string> = { manual: 'Entered by hand', email: 'From forwarder email', api: 'From tracking' };

export function statusLabel(status?: string | null, mode?: string | null) {
  if (status === 'DEPARTED' && mode && DEPARTED_BY_MODE[mode]) return DEPARTED_BY_MODE[mode];
  return (status && STATUS_LABEL[status]) || status || '–';
}

export function statusTone(status: string): Tone {
  if (status === 'EXCEPTION') return 'bad';
  if (FINISHED.includes(status)) return 'ok';
  if (UNDER_WAY.includes(status)) return 'info';
  return 'neutral';
}

export const flagTone = (flag: string): Tone => (flag === 'port free time ending' || flag === 'late' ? 'bad' : 'warn');

export const identifier = (s: Pick<Shipment, 'container_no' | 'awb' | 'reference' | 'id'>) =>
  s.container_no || s.awb || s.reference || `Shipment ${s.id}`;

export const isFinished = (s: Shipment) => FINISHED.includes(s.status);

export function useShipments() {
  return useQuery({
    queryKey: ['shipments'],
    queryFn: async () =>
      (await ok(api.GET('/api/v1/shipments', { params: { query: { include_finished: true } } }))) as {
        statuses: string[];
        shipments: Shipment[];
      },
  });
}

export const statusOptions = (statuses: string[]) =>
  statuses.filter((s) => s !== 'UNKNOWN').map((s) => ({ value: s, label: STATUS_LABEL[s] ?? s }));
