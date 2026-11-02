'use client';

import { useState, type ReactNode } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { EmptyState } from '@/components/data/states';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { executeSystemAction } from '@/lib/api';
import { ProposalCard, proposalOf, type Outcome, type Proposal } from './proposal';


type Spec = {
  type: string;
  title: string;
  effect: string;
  fields: { key: string; label: string; placeholder?: string; multiline?: boolean; numeric?: boolean; optional?: boolean }[];
  submit: string;
};

// The jobs that have no screen of their own elsewhere. Every field is asked
// for: the old task runner filled gaps with sample values (a supplier, a part,
// a PO number), so a blank form quietly checked the wrong thing.
const TASKS: Spec[] = [
  {
    type: 'EXECUTE_INGEST_RFQ_EMAIL',
    title: 'Read a customer’s email into an order',
    effect: 'Runs the email through the same reader as the order mailbox: it creates the order, or holds it in the Inbox when unsure.',
    fields: [
      { key: 'sender_email', label: 'From (email)', placeholder: 'purchasing@customer.com' },
      { key: 'subject_line', label: 'Subject', optional: true },
      { key: 'raw_rfq_body', label: 'Email text', multiline: true },
    ],
    submit: 'Read the email',
  },
  {
    type: 'EXECUTE_MONDAY_PULL',
    title: 'Read items from a monday board',
    effect: 'Reads only; nothing on the board changes. In practice mode the items are examples.',
    fields: [
      { key: 'board_id', label: 'Board number', numeric: true },
      { key: 'limit', label: 'How many items', numeric: true, optional: true },
    ],
    submit: 'Read the board',
  },
];

export function Tasks() {
  const { hasRole } = useAuth();
  if (!hasRole('OPERATOR')) return <EmptyState title="These tasks need an operator account" />;
  return (
    <div className="grid items-start gap-4 lg:grid-cols-2">
      {TASKS.map((t) => (
        <TaskForm key={t.type} spec={t} />
      ))}
      <p className="text-xs text-muted-foreground lg:col-span-2">
        Everything else the assistant used to run has its own screen now: new orders and supplier bills in Orders, quotes in Wear &
        quotes, outreach in Leads, Sage files in Sage & imports, problems in the Inbox.
      </p>
    </div>
  );
}

function TaskForm({ spec }: { spec: Spec }) {
  const [values, setValues] = useState<Record<string, string>>({});
  // A step that changes something comes back as a proposal; a lookup answers at once.
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const run = useMutation({
    mutationFn: async () => {
      const payload = Object.fromEntries(
        spec.fields
          .filter((f) => (values[f.key] ?? '').trim() !== '')
          .map((f) => [f.key, f.numeric ? Number(values[f.key]) : values[f.key].trim()]),
      );
      return (await executeSystemAction(spec.type, payload)) as any;
    },
    onMutate: () => {
      setProposal(null);
      setOutcome(null);
    },
    onSuccess: (res) => setProposal(proposalOf(res)),
  });
  const ready = spec.fields.every((f) => f.optional || (values[f.key] ?? '').trim());
  const res = run.data;

  return (
    <form
      className="grid gap-3 rounded-lg border bg-card p-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) run.mutate();
      }}
    >
      <div className="grid gap-0.5">
        <h2 className="text-sm font-semibold">{spec.title}</h2>
        <p className="text-xs text-pretty text-muted-foreground">{spec.effect}</p>
      </div>
      {spec.fields.map((f) => (
        <Field key={f.key} label={f.optional ? `${f.label} (optional)` : f.label}>
          {f.multiline ? (
            <Textarea rows={5} value={values[f.key] ?? ''} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} required={!f.optional} />
          ) : (
            <Input
              inputMode={f.numeric ? 'numeric' : undefined}
              placeholder={f.placeholder}
              value={values[f.key] ?? ''}
              onChange={(e) => setValues({ ...values, [f.key]: f.numeric ? e.target.value.replace(/\D/g, '') : e.target.value })}
              required={!f.optional}
            />
          )}
        </Field>
      ))}
      <Button type="submit" size="sm" className="justify-self-start" disabled={!ready || run.isPending}>
        {run.isPending && <Loader2 className="animate-spin" />}
        {spec.submit}
      </Button>
      {run.error && (
        <p role="alert" className="text-sm text-bad">
          {run.error.message}
        </p>
      )}
      {proposal ? (
        <ProposalCard
          proposal={proposal}
          onOutcome={(o) => {
            setProposal(null);
            setOutcome(o);
          }}
        />
      ) : outcome ? (
        <Result res={{ status: outcome.status === 'DONE' ? 'success' : outcome.status.toLowerCase(), detail: outcome.text, data: outcome.data }} />
      ) : (
        res && !proposalOf(res) && <Result res={res} />
      )}
    </form>
  );
}

function Result({ res }: { res: any }): ReactNode {
  const ok = !res.status || res.status === 'success';
  return (
    <div className="grid gap-1.5 rounded-md bg-muted/40 p-3 text-[13px]">
      <p className={ok ? 'font-medium' : 'font-medium text-warn'}>{res.detail || (ok ? 'Done.' : res.status)}</p>
      {res.action_taken && <p className="text-muted-foreground">{res.action_taken}</p>}
      {res.data !== undefined && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">Show the result</summary>
          <pre className="mt-1 max-h-64 overflow-auto font-mono text-xs">{JSON.stringify(res.data, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
