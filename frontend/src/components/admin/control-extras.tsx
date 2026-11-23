'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { dateTime } from '@/lib/format';

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T | null> {
  const res = await authFetch(`/api/v1${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });
  if (res.status === 404 && method === 'GET') return null;
  const out = (await res.json().catch(() => null)) as (T & { detail?: string }) | null;
  if (!res.ok)
    throw new Error(
      typeof (out as { detail?: string } | null)?.detail === 'string'
        ? (out as { detail: string }).detail
        : `That didn’t work (${res.status}).`,
    );
  return out;
}

/** A manager’s button: check the data file for damage now. It only reads; it changes nothing. */
export function DatabaseCheck() {
  const { hasRole } = useAuth();
  const [out, setOut] = useState<{ ok?: boolean; text?: string; checked_at?: string } | null>(null);
  const check = useMutation({
    mutationFn: () => call<{ ok?: boolean; text?: string; checked_at?: string }>('/database/check', 'POST', {}),
    onSuccess: setOut,
    onError: (e) => toast.error('The check did not run', { description: e.message }),
  });
  if (!hasRole('MANAGER')) return null;
  return (
    <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="Check the data file">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Is the data file healthy?</h2>
        <Button size="sm" variant="outline" className="ml-auto" disabled={check.isPending} onClick={() => check.mutate()}>
          {check.isPending && <Loader2 className="animate-spin" />} Check it now
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">It reads the data file and looks for damage. It changes nothing.</p>
      {out && (
        <p className={out.ok ? 'text-[13px] text-ok' : 'text-[13px] font-medium text-bad'}>
          {out.text ?? (out.ok ? 'No problems found.' : 'Problems were found.')}
          {out.checked_at ? ` Checked ${dateTime(out.checked_at)}.` : ''}
        </p>
      )}
    </section>
  );
}

type Stats = {
  forms_reviewed?: number;
  fields?: { field: string; method: string; shown: number; kept: number; changed: number; kept_percent?: number }[];
};

const METHOD_WORDS: Record<string, string> = {
  rule: 'a pattern',
  'master data': 'the customer list',
  ai: 'a scan',
  ocr: 'a scan',
};

/** For managers: how often people changed what the app filled in, by field. This shows where the reading can be trusted. */
export function ExtractionStats() {
  const { hasRole } = useAuth();
  const q = useQuery({
    queryKey: ['extraction-stats'],
    queryFn: () => call<Stats>('/ai/extraction-stats?days=90'),
    enabled: hasRole('MANAGER'),
    retry: false,
  });
  if (!hasRole('MANAGER') || !q.data) return null;
  const rows = (q.data.fields ?? []).filter((f) => f.shown > 0);
  return (
    <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="How well the app reads">
      <h2 className="text-sm font-semibold">How well the app reads emails and papers</h2>
      <p className="text-xs text-muted-foreground">
        {q.data.forms_reviewed ?? 0} forms checked in the last 90 days. “Kept” means a person left what the app filled in as it was.
      </p>
      {rows.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">Nothing yet. This fills in as people use “Paste an email to fill this in”.</p>
      ) : (
        <ul className="grid divide-y rounded-md border text-[13px]">
          {rows.slice(0, 12).map((f) => (
            <li key={`${f.field}-${f.method}`} className="flex flex-wrap gap-x-3 px-3 py-1.5">
              <span className="font-medium">{f.field.replace(/^[a-z]+\./, '').replace(/_/g, ' ')}</span>
              <span className="text-xs text-muted-foreground">read from {METHOD_WORDS[f.method] ?? 'the text'}</span>
              <span className="ml-auto text-xs tabular-nums">
                kept {f.kept} of {f.shown}
                {f.kept_percent != null ? ` (${Math.round(f.kept_percent)}%)` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

type Template = { key: string; subject: string; body: string; fields?: string[] };

/** The reply wordings staff start from. Managers change them. Merge fields such as {customer} and {po} are filled in for each email. */
export function MailTemplates() {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [edit, setEdit] = useState<{ subject: string; body: string }>({ subject: '', body: '' });
  const q = useQuery({ queryKey: ['mail-templates'], queryFn: () => call<Template[]>('/mail/templates'), retry: false });
  const save = useMutation({
    mutationFn: (key: string) => call(`/mail/templates/${encodeURIComponent(key)}`, 'PUT', edit),
    onSuccess: () => {
      toast.success('Saved');
      setOpen(null);
      void client.invalidateQueries({ queryKey: ['mail-templates'] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });
  if (!q.data) return null;
  const manager = hasRole('MANAGER');
  return (
    <section className="grid gap-2 rounded-lg border bg-card p-4" aria-label="Reply wordings">
      <h2 className="text-sm font-semibold">Reply wordings</h2>
      <p className="text-xs text-muted-foreground">
        The starting text for replies and follow-ups. Each is still a draft a person checks before it is sent.
      </p>
      <ul className="grid divide-y rounded-md border text-[13px]">
        {q.data.map((t) => (
          <li key={t.key} className="grid gap-1.5 px-3 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{t.key.replace(/_/g, ' ')}</span>
              <span className="min-w-0 truncate text-xs text-muted-foreground">{t.subject}</span>
              {manager && open !== t.key && (
                <Button
                  size="xs"
                  variant="outline"
                  className="ml-auto"
                  onClick={() => {
                    setEdit({ subject: t.subject, body: t.body });
                    setOpen(t.key);
                  }}
                >
                  Change
                </Button>
              )}
            </div>
            {open === t.key ? (
              <div className="grid gap-2">
                <Field label="Subject">
                  <Input value={edit.subject} onChange={(e) => setEdit({ ...edit, subject: e.target.value })} />
                </Field>
                <Field label="Message">
                  <Textarea rows={6} value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} />
                </Field>
                {(t.fields ?? []).length > 0 && (
                  <p className="text-xs text-muted-foreground">Fields you can use: {(t.fields ?? []).map((f) => `{${f}}`).join(', ')}</p>
                )}
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    disabled={save.isPending || !edit.subject.trim() || !edit.body.trim()}
                    onClick={() => save.mutate(t.key)}
                  >
                    Save
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setOpen(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <p className="line-clamp-2 text-xs whitespace-pre-line text-muted-foreground">{t.body}</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
