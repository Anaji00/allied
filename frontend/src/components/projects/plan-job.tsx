'use client';

import { useAppPeople } from '@/components/data/pick';
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { CalendarPlus, Download, Loader2 } from 'lucide-react';
import { Field, NativeSelect } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';
import { day, localIso } from '@/lib/format';

export type Template = {
  id: string;
  name: string;
  milestones: { name: string; offset_days: number; owner_role: string; form?: string }[];
};
type Person = { email: string; name: string };
type Step = { project?: string; milestone: string; due: string; owner_role: string; owner?: string; form?: string };

export const ROLES: [string, string][] = [
  ['engineer', 'Engineer'],
  ['manager', 'Manager'],
  ['sales', 'Sales'],
  ['operations', 'Operations'],
  ['vendor', 'Vendor'],
  ['customer', 'Customer'],
];
export const roleLabel = (r: string) => ROLES.find(([v]) => v === r)?.[1] ?? r;
const BASE = '/api/v1/workspace/project-templates';

export function useTemplates() {
  return useQuery({
    queryKey: ['templates'],
    queryFn: async (): Promise<Template[] | null> => {
      const res = await authFetch(BASE, { cache: 'no-store' });
      return res.ok ? ((await res.json()) as Template[]) : null;
    },
    retry: false,
  });
}

async function fail(res: Response) {
  const detail = await res
    .json()
    .then((j) => j?.detail)
    .catch(() => null);
  return new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
}

/** "Plan a new job": pick a template and a start date, see the dated steps, download them as Excel. Nothing is created in monday. */
export function PlanJob() {
  const templates = useTemplates();
  const [open, setOpen] = useState(false);
  const [id, setId] = useState('');
  const [project, setProject] = useState('');
  const [start, setStart] = useState(() => localIso(new Date()));
  const [owners, setOwners] = useState<Record<string, string>>({});
  const [steps, setSteps] = useState<Step[] | null>(null);
  const people = useAppPeople();

  const list = templates.data ?? [];
  const chosen = list.find((t) => t.id === (id || list[0]?.id));
  const roles = [...new Set((chosen?.milestones ?? []).map((m) => m.owner_role))];
  const body = () => ({
    start,
    project: project.trim() || undefined,
    owners: Object.fromEntries(Object.entries(owners).filter(([, v]) => v)),
  });

  const preview = useMutation({
    mutationFn: async () => {
      const res = await authFetch(`${BASE}/${encodeURIComponent(chosen!.id)}/plan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body()),
      });
      if (!res.ok) throw await fail(res);
      return (await res.json()) as Step[];
    },
    onSuccess: setSteps,
    onError: (e) => toast.error('The plan couldn’t be made', { description: e.message }),
  });
  const file = useMutation({
    mutationFn: async (format: 'xlsx' | 'csv' | 'pdf') => {
      const res = await authFetch(`${BASE}/${encodeURIComponent(chosen!.id)}/plan/file?format=${format}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body()),
      });
      if (!res.ok) throw await fail(res);
      const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(res.headers.get('content-disposition') || '')?.[1];
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement('a');
      a.href = url;
      a.download = name ? decodeURIComponent(name) : `job-plan.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    onError: (e) => toast.error('The download didn’t work', { description: e.message }),
  });

  if (!templates.data) return null;

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <CalendarPlus /> Plan a new job
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Plan a new job</DialogTitle>
            <DialogDescription>
              Dates every step from the start date. This only makes a plan to download and import into monday; nothing is created there.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Template">
              <NativeSelect
                value={chosen?.id ?? ''}
                onChange={(e) => {
                  setId(e.target.value);
                  setSteps(null);
                }}
              >
                {list.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Starts on">
              <Input
                type="date"
                value={start}
                onChange={(e) => {
                  setStart(e.target.value);
                  setSteps(null);
                }}
              />
            </Field>
            <Field label="Job name (optional)">
              <Input value={project} onChange={(e) => setProject(e.target.value)} placeholder="Customer - PO - Part" />
            </Field>
          </div>
          {roles.length > 0 && (
            <div className="grid gap-2">
              <p className="text-sm font-medium">Who does each part</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {roles.map((r) => (
                  <Field key={r} label={roleLabel(r)}>
                    <NativeSelect value={owners[r] ?? ''} onChange={(e) => setOwners({ ...owners, [r]: e.target.value })}>
                      <option value="">Nobody yet</option>
                      {(people.data ?? []).map((p) => (
                        <option key={p.email || p.name} value={p.email} disabled={!p.email}>
                          {p.email ? p.name : `${p.name} (no email yet)`}
                        </option>
                      ))}
                    </NativeSelect>
                  </Field>
                ))}
              </div>
            </div>
          )}
          {steps && (
            <div className="overflow-x-auto rounded-lg border">
              <table className="w-full min-w-[420px] text-[13px]">
                <thead className="text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">Step</th>
                    <th className="px-3 py-2 font-medium">Due</th>
                    <th className="px-3 py-2 font-medium">Who</th>
                  </tr>
                </thead>
                <tbody>
                  {steps.map((s, i) => (
                    <tr key={i} className="border-t">
                      <td className="px-3 py-1.5">{s.milestone}</td>
                      <td className="px-3 py-1.5 whitespace-nowrap">{day(s.due, { weekday: true })}</td>
                      <td className="px-3 py-1.5">{s.owner || roleLabel(s.owner_role)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <DialogFooter className="flex-wrap">
            {steps && (
              <>
                <Button variant="outline" disabled={file.isPending} onClick={() => file.mutate('xlsx')}>
                  {file.isPending ? <Loader2 className="animate-spin" /> : <Download />} Excel
                </Button>
                <Button variant="ghost" disabled={file.isPending} onClick={() => file.mutate('csv')}>
                  CSV
                </Button>
                <Button variant="ghost" disabled={file.isPending} onClick={() => file.mutate('pdf')}>
                  PDF
                </Button>
              </>
            )}
            <Button disabled={!chosen || !start || preview.isPending} onClick={() => preview.mutate()}>
              {preview.isPending && <Loader2 className="animate-spin" />}
              {steps ? 'Update the plan' : 'Show the plan'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
