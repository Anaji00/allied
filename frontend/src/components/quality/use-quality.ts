'use client';

import { queryOptions, useQuery } from '@tanstack/react-query';
import type { Tone } from '@/components/data/tone-badge';
import { ApiError, api, ok } from '@/lib/api/client';

export type NCR = {
  ncr_id: string;
  heat_number: string;
  item_id: string;
  supplier: string;
  material_spec: string;
  failure_reasons: string[];
  severity: string;
  status: 'QUARANTINED' | 'REJECTED' | 'CONCESSION_APPROVED' | 'CLOSED' | string;
  quarantined_by: string;
  created_at: string;
  resolved_at?: string | null;
  resolved_by?: string | null;
  disposition_notes?: string | null;
};

type Num = number | string | null | undefined;

export type MTR = {
  heat_number: string;
  item_id: string;
  material_spec: string;
  mill_supplier: string;
  chemical_analysis: Partial<Record<'carbon_pct' | 'chromium_pct' | 'manganese_pct' | 'silicon_pct' | 'molybdenum_pct' | 'nickel_pct' | 'phosphorus_pct' | 'sulfur_pct', Num>>;
  mechanical_tests: { yield_strength_psi?: Num; tensile_strength_psi?: Num; elongation_pct?: Num; hardness_rating?: string | null };
  verified_compliant: boolean;
  compliance_notes: string[];
};

export const NCR_STATUS: Record<string, { label: string; tone: Tone }> = {
  QUARANTINED: { label: 'On hold', tone: 'bad' },
  REJECTED: { label: 'Rejected, returned', tone: 'neutral' },
  CONCESSION_APPROVED: { label: 'Accepted with concession', tone: 'ok' },
  CLOSED: { label: 'Closed', tone: 'neutral' },
};

export const DECISIONS = [
  { value: 'RETURN_TO_SUPPLIER', label: 'Reject the lot and return it to the supplier' },
  { value: 'APPROVE_CONCESSION', label: 'Accept it for non-critical use (concession)' },
  { value: 'REQUEST_LAB_RETEST', label: 'Keep it on hold and send it for a lab retest' },
] as const;

export const CHEMISTRY = [
  ['carbon_pct', 'Carbon (C)'],
  ['chromium_pct', 'Chromium (Cr)'],
  ['manganese_pct', 'Manganese (Mn)'],
  ['silicon_pct', 'Silicon (Si)'],
  ['molybdenum_pct', 'Molybdenum (Mo)'],
  ['nickel_pct', 'Nickel (Ni)'],
  ['phosphorus_pct', 'Phosphorus (P)'],
  ['sulfur_pct', 'Sulfur (S)'],
] as const;

/** The parts that have published limits; any other part needs a manual review. */
export const KNOWN_PARTS = ['PLATE-300XL-38', 'PLATE-A36-12', 'BEAM-W8X31', 'HOSE-CERAMIC-4IN'];

export function useNCRs() {
  return useQuery({
    queryKey: ['ncrs'],
    queryFn: async () => (await ok(api.GET('/api/v1/compliance/ncr'))) as NCR[],
  });
}

/** One hold in full; shared by the panel and the hover prefetch. */
export const ncrQuery = (id: string) =>
  queryOptions({
    queryKey: ['ncrs', id],
    queryFn: async () => (await ok(api.GET('/api/v1/compliance/ncr/{ncr_id}', { params: { path: { ncr_id: id } } }))) as NCR,
  });

export function useNCR(id: string | null) {
  return useQuery({ ...ncrQuery(id ?? ''), enabled: Boolean(id) });
}

/** The certificate on file for a heat; null when there isn't one. */
export function useMTR(heat: string) {
  return useQuery({
    queryKey: ['mtr', heat],
    enabled: Boolean(heat),
    queryFn: async () => {
      try {
        return (await ok(api.GET('/api/v1/compliance/mtr/{heat_number}', { params: { path: { heat_number: heat } } }))) as MTR;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
  });
}

export const psi = (v: Num) => (v == null || v === '' ? '–' : `${Number(v).toLocaleString()} PSI`);
