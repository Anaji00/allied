'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field, PanelHeading } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { leads, type Lead } from './api';
import { useLeadSettings } from './use-leads';

type Found = { name: string; title: string; company: string; snippet: string; profile_url: string };
type Many = { added: number; duplicates: number; errors: { row: number; error: string }[] };

const COLUMNS = ['name', 'title', 'company', 'email', 'phone', 'industry', 'location', 'notes'] as const;
const BLANK = { name: '', title: '', company: '', email: '', phone: '', industry: '', notes: '' };

/** Plain CSV with a header row. Quoted commas are kept together; nothing fancier is needed for a short list. */
function parseCsv(text: string): Record<string, string>[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) return [];
  const cells = (line: string) => (line.match(/("([^"]|"")*"|[^,]*)(,|$)/g) ?? []).map((c) => c.replace(/,$/, '').replace(/^"|"$/g, '').replace(/""/g, '"').trim());
  const head = cells(lines[0]).map((h) => h.toLowerCase().replace(/\s+/g, '_'));
  return lines.slice(1).map((line) => {
    const row: Record<string, string> = {};
    cells(line).forEach((v, i) => {
      if (head[i] && (COLUMNS as readonly string[]).includes(head[i]) && v) row[head[i]] = v;
    });
    return row;
  });
}

/** Adding is always a person's click. By hand or from a file is the main way; web search is optional and off unless the owner turns it on. */
export function Find() {
  const client = useQueryClient();
  const settings = useLeadSettings();
  const [form, setForm] = useState(BLANK);
  const [csv, setCsv] = useState('');
  const refresh = () => void client.invalidateQueries({ queryKey: ['leads'] });

  const addOne = useMutation({
    mutationFn: () => leads<Lead & { duplicate?: boolean }>('', 'POST', { ...form, source: 'manual' }),
    onSuccess: (r) => {
      if (r.duplicate) toast.message('Already on your list', { description: 'Nothing was added twice.' });
      else {
        toast.success('Added to your leads');
        setForm(BLANK);
      }
      refresh();
    },
    onError: (e) => toast.error('Not added', { description: e.message }),
  });
  const addMany = useMutation({
    mutationFn: () => leads<Many>('', 'POST', { leads: parseCsv(csv).map((r) => ({ ...r, source: 'import' })) }),
    onSuccess: (r) => {
      toast.success(`${r.added} added, ${r.duplicates} already on your list`, {
        description: r.errors.length ? `${r.errors.length} line${r.errors.length === 1 ? '' : 's'} skipped: ${r.errors[0].error}` : undefined,
      });
      setCsv('');
      refresh();
    },
    onError: (e) => toast.error('Nothing was added', { description: e.message }),
  });
  const rows = parseCsv(csv).length;
  const set = (k: keyof typeof BLANK) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="grid min-w-0 gap-4 lg:grid-cols-2">
      <section className="grid content-start gap-3 rounded-lg border bg-card p-4" aria-label="Add one lead">
        <PanelHeading>Add one by hand</PanelHeading>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name">
            <Input value={form.name} onChange={set('name')} />
          </Field>
          <Field label="Job title">
            <Input value={form.title} onChange={set('title')} />
          </Field>
          <Field label="Company">
            <Input value={form.company} onChange={set('company')} />
          </Field>
          <Field label="Email">
            <Input type="email" value={form.email} onChange={set('email')} />
          </Field>
          <Field label="Phone">
            <Input value={form.phone} onChange={set('phone')} />
          </Field>
          <Field label="Industry">
            <Input value={form.industry} onChange={set('industry')} />
          </Field>
        </div>
        <Field label="Notes" hint="Where you met, what they need.">
          <Textarea rows={2} value={form.notes} onChange={set('notes')} />
        </Field>
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={addOne.isPending || !(form.company.trim() || form.name.trim() || form.email.trim())} onClick={() => addOne.mutate()}>
            {addOne.isPending && <Loader2 className="animate-spin" />} Add to leads
          </Button>
          <span className="text-xs text-muted-foreground">Give a company, a name or an email.</span>
        </div>
      </section>

      <section className="grid content-start gap-3 rounded-lg border bg-card p-4" aria-label="Add from a file">
        <PanelHeading>Add from a file</PanelHeading>
        <Field label="Paste the rows" hint={`First row names the columns: ${COLUMNS.join(', ')}. Copy them from a spreadsheet saved as CSV.`}>
          <Textarea rows={6} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={'name,title,company,email\nAnn Lee,Plant Manager,Acme Cement,ann@acme.example'} />
        </Field>
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={addMany.isPending || rows === 0} onClick={() => addMany.mutate()}>
            {addMany.isPending && <Loader2 className="animate-spin" />} Add {rows > 0 ? rows : ''} to leads
          </Button>
          <span className="text-xs text-muted-foreground">People already on the list are skipped.</span>
        </div>
      </section>

      <Search key={String(Boolean(settings.data))} enabled={Boolean(settings.data?.search_enabled)} loaded={Boolean(settings.data)} onAdded={refresh} defaults={settings.data} />
    </div>
  );
}

function Search({
  enabled,
  loaded,
  onAdded,
  defaults,
}: {
  enabled: boolean;
  loaded: boolean;
  onAdded: () => void;
  defaults?: { titles: string[]; industries: string[] } | null;
}) {
  const [titles, setTitles] = useState(defaults?.titles.join(', ') ?? '');
  const [industries, setIndustries] = useState(defaults?.industries.join(', ') ?? '');
  const [where, setWhere] = useState('');
  const [found, setFound] = useState<Found[] | null>(null);
  const split = (t: string) => t.split(',').map((s) => s.trim()).filter(Boolean);

  const search = useMutation({
    mutationFn: () => leads<Found[]>('/scrape', 'POST', { job_titles: split(titles), industries: split(industries), location: where.trim() }),
    onSuccess: (r) => setFound(r),
    onError: (e) => toast.error('The search didn’t run', { description: e.message }),
  });
  const add = useMutation({
    mutationFn: (f: Found) => leads('', 'POST', { name: f.name, title: f.title, company: f.company, profile_url: f.profile_url, notes: f.snippet, source: 'search' }),
    onSuccess: () => {
      toast.success('Added to your leads');
      onAdded();
    },
    onError: (e) => toast.error('Not added', { description: e.message }),
  });

  return (
    <section className="grid content-start gap-3 rounded-lg border bg-card p-4 lg:col-span-2" aria-label="Search the web">
      <PanelHeading>Search the web</PanelHeading>
      {!loaded ? (
        <p className="text-[13px] text-muted-foreground">Not ready yet.</p>
      ) : !enabled ? (
        <p className="text-[13px] text-muted-foreground">
          Web search is switched off. Adding by hand or from a file is the main way to find leads. The owner can switch search on after checking that it fits
          the terms of the sites it reads.
        </p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Job titles" hint="Separated by commas.">
              <Input value={titles} onChange={(e) => setTitles(e.target.value)} />
            </Field>
            <Field label="Industries">
              <Input value={industries} onChange={(e) => setIndustries(e.target.value)} />
            </Field>
            <Field label="Where">
              <Input value={where} onChange={(e) => setWhere(e.target.value)} />
            </Field>
          </div>
          <div>
            <Button size="sm" disabled={search.isPending || !split(titles).length || !split(industries).length} onClick={() => search.mutate()}>
              {search.isPending && <Loader2 className="animate-spin" />} Search
            </Button>
          </div>
          {found && found.length === 0 && <p className="text-[13px] text-muted-foreground">Nothing matched.</p>}
          {found && found.length > 0 && (
            <ul className="divide-y rounded-md border">
              {found.map((f) => (
                <li key={f.profile_url || f.name} className="flex flex-wrap items-center justify-between gap-2 p-2 text-[13px]">
                  <span className="min-w-0">
                    <span className="font-medium">{f.name || 'Unnamed'}</span> <span className="text-muted-foreground">{[f.title, f.company].filter(Boolean).join(' · ')}</span>
                  </span>
                  <Button size="xs" variant="outline" disabled={add.isPending} onClick={() => add.mutate(f)}>
                    Add to leads
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
