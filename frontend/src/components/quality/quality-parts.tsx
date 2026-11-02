'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Facts } from '@/components/data/detail-panel';
import { Field, NativeSelect, PanelHeading } from '@/components/data/filters';
import { ErrorState, RowsSkeleton } from '@/components/data/states';
import { ToneBadge } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { api, ok } from '@/lib/api/client';
import { optimistic } from '@/lib/optimistic';
import { dateTime } from '@/lib/format';
import { CHEMISTRY, DECISIONS, KNOWN_PARTS, NCR_STATUS, psi, useNCR, type MTR, type NCR } from './use-quality';

export function NcrDetail({ id, onLookUp }: { id: string; onLookUp: (heat: string) => void }) {
  const { hasRole } = useAuth();
  const { data: ncr, isPending, error, refetch } = useNCR(id);
  if (isPending) return <RowsSkeleton rows={4} />;
  if (error || !ncr) return <ErrorState error={error} onRetry={() => void refetch()} />;
  const status = NCR_STATUS[ncr.status] ?? { label: ncr.status, tone: 'neutral' as const };

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap gap-1.5">
        <ToneBadge tone={status.tone}>{status.label}</ToneBadge>
        <ToneBadge tone={ncr.severity === 'CRITICAL' ? 'bad' : 'warn'}>{ncr.severity.charAt(0) + ncr.severity.slice(1).toLowerCase()}</ToneBadge>
      </div>

      <div className="grid gap-1.5">
        <PanelHeading>Why it was held</PanelHeading>
        {ncr.failure_reasons.length ? (
          <ul className="grid list-disc gap-1 pl-5 text-[13px]">
            {ncr.failure_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No reasons recorded.</p>
        )}
      </div>

      <Facts
        items={[
          ['Heat', <span key="h" className="font-mono text-xs">{ncr.heat_number}</span>],
          ['Part', <span key="p" className="font-mono text-xs">{ncr.item_id}</span>],
          ['Supplier', ncr.supplier],
          ['Spec', ncr.material_spec],
          ['Held by', ncr.quarantined_by],
          ['Raised', dateTime(ncr.created_at)],
          ...(ncr.resolved_at
            ? ([
                ['Decided', dateTime(ncr.resolved_at)],
                ['By', ncr.resolved_by ?? '–'],
              ] as [string, string][])
            : []),
        ]}
      />
      {ncr.disposition_notes && (
        <div className="grid gap-1">
          <PanelHeading>{ncr.status === 'QUARANTINED' ? 'Notes' : 'Decision'}</PanelHeading>
          <p className="text-[13px] whitespace-pre-line">{ncr.disposition_notes}</p>
        </div>
      )}

      <Button variant="outline" size="sm" className="justify-self-start" onClick={() => onLookUp(ncr.heat_number)}>
        See the mill certificate
      </Button>

      {ncr.status === 'QUARANTINED' && hasRole('OPERATOR') && <DecisionForm key={ncr.ncr_id} ncr={ncr} />}
    </div>
  );
}

// What each decision does to the hold; a lab retest keeps it on hold.
const OUTCOME: Record<string, string> = {
  RETURN_TO_SUPPLIER: 'REJECTED',
  APPROVE_CONCESSION: 'CONCESSION_APPROVED',
  REQUEST_LAB_RETEST: 'QUARANTINED',
};

function DecisionForm({ ncr }: { ncr: NCR }) {
  const client = useQueryClient();
  const { employeeEmail } = useAuth();
  const [action, setAction] = useState<string>(DECISIONS[0].value);
  const [notes, setNotes] = useState('');
  const decided = (n: NCR): NCR =>
    n.ncr_id !== ncr.ncr_id
      ? n
      : {
          ...n,
          status: OUTCOME[action] ?? n.status,
          disposition_notes: `${action}: ${notes.trim()}`,
          resolved_at: OUTCOME[action] === 'QUARANTINED' ? n.resolved_at : new Date().toISOString(),
          resolved_by: employeeEmail ?? n.resolved_by,
        };
  // The list and the open hold both live under ['ncrs'].
  const instant = optimistic<void>(client, ['ncrs'], (old: NCR[] | NCR) => (Array.isArray(old) ? old.map(decided) : decided(old)));
  const decide = useMutation({
    mutationFn: async () =>
      ok(
        api.POST('/api/v1/compliance/ncr/{ncr_id}/disposition', {
          params: { path: { ncr_id: ncr.ncr_id } },
          body: { action, disposition_notes: notes.trim() },
        }),
      ),
    onMutate: instant.onMutate,
    onSuccess: () => toast.success(`Decision saved for heat ${ncr.heat_number}`),
    onError: (e, _v, context) => {
      instant.rollback(context);
      toast.error('The decision wasn’t saved', { description: e.message });
    },
    onSettled: instant.onSettled,
  });

  return (
    <form
      className="grid gap-3 rounded-md border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (notes.trim()) decide.mutate();
      }}
    >
      <PanelHeading>Decide what happens to this lot</PanelHeading>
      <Field label="Decision">
        <NativeSelect value={action} onChange={(e) => setAction(e.target.value)}>
          {DECISIONS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field label="Reason" hint="Saved with the record and in the audit trail under your name.">
        <Textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} required />
      </Field>
      <Button type="submit" size="sm" className="justify-self-start" disabled={!notes.trim() || decide.isPending}>
        {decide.isPending && <Loader2 className="animate-spin" />}
        Save decision
      </Button>
    </form>
  );
}

/** A mill certificate: who made it, what it measured, and whether that's within limits. */
export function MtrCard({ mtr }: { mtr: MTR }) {
  const chem = CHEMISTRY.filter(([k]) => mtr.chemical_analysis?.[k] != null);
  const mech = mtr.mechanical_tests ?? {};
  return (
    <div className="grid gap-4 rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-start gap-2">
        <div className="grid gap-0.5">
          <p className="font-mono text-sm font-semibold">{mtr.heat_number}</p>
          <p className="text-xs text-muted-foreground">{mtr.material_spec}</p>
        </div>
        <ToneBadge tone={mtr.verified_compliant ? 'ok' : 'bad'} className="ml-auto">
          {mtr.verified_compliant ? 'Within limits' : 'Out of limits'}
        </ToneBadge>
      </div>
      <Facts
        items={[
          ['Part', <span key="p" className="font-mono text-xs">{mtr.item_id}</span>],
          ['Mill', mtr.mill_supplier],
        ]}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid content-start gap-1.5">
          <PanelHeading>Chemistry (% by weight)</PanelHeading>
          {chem.length ? (
            <Facts items={chem.map(([k, label]) => [label, `${mtr.chemical_analysis[k]}%`])} />
          ) : (
            <p className="text-sm text-muted-foreground">Not recorded on this certificate.</p>
          )}
        </div>
        <div className="grid content-start gap-1.5">
          <PanelHeading>Mechanical tests</PanelHeading>
          <Facts
            items={[
              ['Hardness', mech.hardness_rating || '–'],
              ['Yield', psi(mech.yield_strength_psi)],
              ['Tensile', psi(mech.tensile_strength_psi)],
              ['Elongation', mech.elongation_pct != null ? `${mech.elongation_pct}%` : '–'],
            ]}
          />
        </div>
      </div>
      {mtr.compliance_notes.length > 0 && (
        <ul className="grid gap-1 text-[13px] text-muted-foreground">
          {mtr.compliance_notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const CHEM_INPUTS = [
  ['carbon', 'Carbon %'],
  ['chromium', 'Chromium %'],
  ['manganese', 'Manganese %'],
  ['silicon', 'Silicon %'],
] as const;

export function RegisterCertDialog({
  open,
  onOpenChange,
  onRegistered,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRegistered: (mtr: MTR) => void;
}) {
  const client = useQueryClient();
  const [heat, setHeat] = useState('');
  const [part, setPart] = useState('');
  const [hardness, setHardness] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});

  const register = useMutation({
    mutationFn: async () => {
      const chemistry = Object.fromEntries(
        Object.entries(values)
          .filter(([, v]) => v.trim() !== '')
          .map(([k, v]) => [k, Number(v)]),
      );
      return (await ok(
        api.POST('/api/v1/compliance/mtr/verify', {
          body: { heat_number: heat.trim(), item_id: part.trim(), chemistry_input: chemistry, hardness_input: hardness.trim() || null },
        }),
      )) as MTR;
    },
    onSuccess: (mtr) => {
      if (mtr.verified_compliant) toast.success(`Heat ${mtr.heat_number} is within limits`);
      else toast.warning(`Heat ${mtr.heat_number} is out of limits`, { description: 'The lot is on hold and a non-conformance report was raised.' });
      void client.invalidateQueries({ queryKey: ['mtr', mtr.heat_number] });
      void client.invalidateQueries({ queryKey: ['ncrs'] });
      setHeat('');
      setPart('');
      setHardness('');
      setValues({});
      onOpenChange(false);
      onRegistered(mtr);
    },
    onError: (e) => toast.error('The certificate wasn’t registered', { description: e.message }),
  });

  const ready = heat.trim() && part.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Register a mill certificate</DialogTitle>
          <DialogDescription>
            Type in the figures from the supplier’s certificate. They’re checked against the part’s limits; anything out of
            limits puts the lot on hold. This replaces any certificate already on file for the heat.
          </DialogDescription>
        </DialogHeader>
        <form
          id="register-cert"
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) register.mutate();
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Heat number">
              <Input value={heat} onChange={(e) => setHeat(e.target.value)} placeholder="HT-300XL-8819" required />
            </Field>
            <Field label="Part" hint="Parts without published limits need a manual review.">
              <Input value={part} onChange={(e) => setPart(e.target.value)} list="known-parts" required />
              <datalist id="known-parts">
                {KNOWN_PARTS.map((p) => (
                  <option key={p} value={p} />
                ))}
              </datalist>
            </Field>
          </div>
          <fieldset className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <legend className="mb-1.5 text-sm font-medium">Chemistry</legend>
            {CHEM_INPUTS.map(([key, label]) => (
              <Field key={key} label={<span className="font-normal text-muted-foreground">{label}</span>}>
                <Input
                  type="number"
                  step="any"
                  min="0"
                  inputMode="decimal"
                  value={values[key] ?? ''}
                  onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                />
              </Field>
            ))}
          </fieldset>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Field label="Hardness">
              <Input value={hardness} onChange={(e) => setHardness(e.target.value)} placeholder="58-62 HRC" />
            </Field>
            <Field label="Yield (PSI)">
              <Input type="number" min="0" inputMode="numeric" value={values.yield_strength_psi ?? ''} onChange={(e) => setValues((v) => ({ ...v, yield_strength_psi: e.target.value }))} />
            </Field>
            <Field label="Tensile (PSI)">
              <Input type="number" min="0" inputMode="numeric" value={values.tensile_strength_psi ?? ''} onChange={(e) => setValues((v) => ({ ...v, tensile_strength_psi: e.target.value }))} />
            </Field>
          </div>
          <p className="text-xs text-muted-foreground">Leave a figure blank if the certificate doesn’t give it. Missing figures are reported, never assumed.</p>
        </form>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="register-cert" disabled={!ready || register.isPending}>
            {register.isPending && <Loader2 className="animate-spin" />}
            Check and register
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
