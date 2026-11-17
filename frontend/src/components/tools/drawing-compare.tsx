'use client';

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { FileUp, Loader2 } from 'lucide-react';
import { ToneBadge, type Tone } from '@/components/data/tone-badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

type Facts = {
  filename?: string;
  pages?: number;
  drawing_number?: string | null;
  revision?: string | null;
  material?: string | null;
  scale?: string | null;
  units?: string | null;
  projection?: string | null;
  standards?: string[];
  has_chinese?: boolean;
  chinese_terms?: { zh: string; en: string }[];
};
type Region = { x: number; y: number; w: number; h: number; what: string };
type Result = {
  verdict: 'identical' | 'minor differences' | 'different';
  changed_percent: number;
  warnings: string[];
  first: Facts;
  second: Facts;
  turned_second_by?: number;
  regions: Region[];
  text: { only_on_first: string[]; only_on_second: string[] };
  numbers: { only_on_first: string[]; only_on_second: string[] };
  legend?: string;
  overlay_png_base64?: string;
  geometry?: {
    notes?: string[];
    size_mm?: { first: [number, number]; second: [number, number]; changed?: boolean };
    circle_diameters_mm?: { only_on_first: number[]; only_on_second: number[] };
    dimensions_mm?: { only_on_first: number[]; only_on_second: number[] };
  } | null;
};

const VERDICT: Record<Result['verdict'], { tone: Tone; text: string }> = {
  identical: { tone: 'ok', text: 'The drawings match' },
  'minor differences': { tone: 'warn', text: 'Small differences' },
  different: { tone: 'bad', text: 'The drawings are different' },
};
const BOX: Record<string, string> = {
  'only on the first': 'border-bad',
  'only on the second': 'border-info',
  changed: 'border-warn',
};

const ROWS: [string, keyof Facts][] = [
  ['Drawing number', 'drawing_number'],
  ['Revision', 'revision'],
  ['Material', 'material'],
  ['Scale', 'scale'],
  ['Units', 'units'],
  ['Projection', 'projection'],
  ['Pages', 'pages'],
];
const show = (v: unknown) => (Array.isArray(v) ? v.join(', ') : v == null || v === '' ? '–' : String(v));

const OK = /\.(pdf|png|jpe?g|tiff?|bmp|webp|dxf)$/i;

/** Plain advice for a file the server can't open, said at once instead of after an upload. */
function advice(name: string) {
  if (OK.test(name)) return null;
  if (/\.dwg$/i.test(name))
    return `${name} is a DWG file, and DWG can’t be opened here. In your CAD program use Save As or Export to DXF (any version), or print to PDF.`;
  if (/\.(step|stp|igs|iges)$/i.test(name))
    return `${name} is a 3D model, which can’t be opened here. Export the drawing views from it as DXF or PDF.`;
  return `${name}: that kind of file can’t be read. Use PDF, PNG, JPG, TIFF or DXF (for DWG, save as DXF first).`;
}

function Drop({ label, file, onFile }: { label: string; file: File | null; onFile: (f: File | null) => void }) {
  const [over, setOver] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const take = (f: File | null) => {
    const why = f ? advice(f.name) : null;
    setProblem(why);
    onFile(why ? null : f);
  };
  return (
    <label
      className={cn(
        'grid cursor-pointer place-items-center gap-1 rounded-2xl border border-dashed p-5 text-center text-sm transition-colors hover:bg-muted/50',
        over && 'border-foreground bg-muted/50',
        file && 'border-solid',
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files?.[0] ?? null);
      }}
    >
      <FileUp className="size-5 text-muted-foreground" aria-hidden="true" />
      <span className="font-medium">{label}</span>
      <span className="max-w-full truncate text-xs text-muted-foreground">
        {file ? file.name : 'Drop a PDF, picture or DXF here, or choose'}
      </span>
      {problem && (
        <span role="alert" className="max-w-xs text-xs text-warn">
          {problem}
        </span>
      )}
      <input
        type="file"
        accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.dxf"
        className="sr-only"
        aria-label={label}
        onChange={(e) => take(e.target.files?.[0] ?? null)}
      />
    </label>
  );
}

/** Two drawings in, one overlay out: red is only on the first, blue only on the second, grey the same. */
export function DrawingCompare() {
  const [first, setFirst] = useState<File | null>(null);
  const [second, setSecond] = useState<File | null>(null);
  const [pageFirst, setPageFirst] = useState('1');
  const [pageSecond, setPageSecond] = useState('1');
  const [boxes, setBoxes] = useState(true);
  const isPdf = (f: File | null) => f?.type === 'application/pdf' || /\.pdf$/i.test(f?.name ?? '');

  const run = useMutation({
    mutationFn: async (): Promise<Result> => {
      const form = new FormData();
      form.append('first', first!);
      form.append('second', second!);
      form.append('page_first', pageFirst || '1');
      form.append('page_second', pageSecond || '1');
      const res = await authFetch('/api/v1/drawings/compare', { method: 'POST', body: form });
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `The comparison didn’t work (${res.status}).`);
      return body as Result;
    },
    onError: (e) => toast.error('The drawings weren’t compared', { description: e.message }),
  });
  const r = run.data;
  const v = r ? VERDICT[r.verdict] : null;

  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">
        Drop their drawing and ours. PDF, picture and DXF files work; for a DWG or STEP file, export it as DXF or PDF first. The app lines
        them up, overlays them and lists what changed.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="grid gap-2">
          <Drop label="First drawing" file={first} onFile={setFirst} />
          {isPdf(first) && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Page{' '}
              <Input
                className="h-8 w-16"
                inputMode="numeric"
                value={pageFirst}
                onChange={(e) => setPageFirst(e.target.value.replace(/\D/g, ''))}
              />
            </label>
          )}
        </div>
        <div className="grid gap-2">
          <Drop label="Second drawing" file={second} onFile={setSecond} />
          {isPdf(second) && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Page{' '}
              <Input
                className="h-8 w-16"
                inputMode="numeric"
                value={pageSecond}
                onChange={(e) => setPageSecond(e.target.value.replace(/\D/g, ''))}
              />
            </label>
          )}
        </div>
      </div>
      <Button className="justify-self-start" disabled={!first || !second || run.isPending} onClick={() => run.mutate()}>
        {run.isPending && <Loader2 className="animate-spin" />}
        {run.isPending ? 'Comparing…' : 'Compare'}
      </Button>

      {r && v && (
        <div className="grid gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <ToneBadge tone={v.tone}>{v.text}</ToneBadge>
            <span className="text-sm text-muted-foreground">{r.changed_percent}% of the drawing changed</span>
          </div>

          {r.warnings.length > 0 && (
            <ul role="alert" className="grid gap-1 rounded-lg bg-warn-soft p-3 text-sm text-warn">
              {r.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}

          {r.geometry && <Geometry g={r.geometry} />}

          {r.overlay_png_base64 && (
            <div className="grid gap-2">
              <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                <span>{r.legend || 'Red: only on the first. Blue: only on the second. Grey: the same.'}</span>
                <label className="ml-auto flex items-center gap-1.5">
                  <input type="checkbox" className="size-3.5" checked={boxes} onChange={(e) => setBoxes(e.target.checked)} />
                  Outline the changes
                </label>
              </div>
              <div className="relative overflow-hidden rounded-lg border bg-white">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`data:image/png;base64,${r.overlay_png_base64}`}
                  alt="The two drawings on top of each other"
                  className="block h-auto w-full"
                />
                {boxes &&
                  r.regions.map((g, i) => (
                    <span
                      key={i}
                      title={g.what}
                      className={cn('pointer-events-none absolute rounded-sm border-2', BOX[g.what] ?? 'border-warn')}
                      style={{ left: `${g.x}%`, top: `${g.y}%`, width: `${g.w}%`, height: `${g.h}%` }}
                    />
                  ))}
              </div>
            </div>
          )}

          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[420px] text-[13px]">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium" />
                  <th className="px-3 py-2 font-medium">First{r.first.filename ? ` · ${r.first.filename}` : ''}</th>
                  <th className="px-3 py-2 font-medium">Second{r.second.filename ? ` · ${r.second.filename}` : ''}</th>
                </tr>
              </thead>
              <tbody>
                {[...ROWS, ['Standards', 'standards'] as [string, keyof Facts]].map(([label, key]) => {
                  const a = show(r.first[key]);
                  const b = show(r.second[key]);
                  return (
                    <tr key={key} className={cn('border-t', a !== b && 'bg-warn-soft/60')}>
                      <th scope="row" className="px-3 py-1.5 text-left font-medium text-muted-foreground">
                        {label}
                      </th>
                      <td className="px-3 py-1.5">{a}</td>
                      <td className="px-3 py-1.5">{b}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {[r.first, r.second].some((f) => f.chinese_terms?.length) && (
            <div className="grid gap-1.5">
              <h3 className="text-xs font-medium text-muted-foreground">Chinese terms on the drawings</h3>
              <div className="flex flex-wrap gap-1.5">
                {[...new Map([...(r.first.chinese_terms ?? []), ...(r.second.chinese_terms ?? [])].map((t) => [t.zh, t])).values()].map(
                  (t) => (
                    <span key={t.zh} className="rounded-full border bg-card px-2.5 py-1 text-xs">
                      {t.zh} = {t.en}
                    </span>
                  ),
                )}
              </div>
            </div>
          )}

          <div className="grid gap-3 md:grid-cols-2">
            <Only title="Only on the first drawing" text={r.text.only_on_first} numbers={r.numbers.only_on_first} />
            <Only title="Only on the second drawing" text={r.text.only_on_second} numbers={r.numbers.only_on_second} />
          </div>
        </div>
      )}
    </div>
  );
}

function Only({ title, text, numbers }: { title: string; text: string[]; numbers: string[] }) {
  if (!text.length && !numbers.length) return null;
  return (
    <section className="grid content-start gap-1.5 rounded-lg border bg-card p-3">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {numbers.length > 0 && (
        <p className="text-[13px]">
          <span className="text-muted-foreground">Numbers: </span>
          <span className="font-mono text-xs">{numbers.join('  ')}</span>
        </p>
      )}
      {text.length > 0 && (
        <ul className="grid list-disc gap-0.5 pl-5 text-[13px]">
          {text.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

const mm = (n: number) => `${Number.isInteger(n) ? n : n.toFixed(2)} mm`;
const size = (v?: [number, number]) => (v ? `${mm(v[0])} × ${mm(v[1])}` : '–');

/** For two DXF files the numbers come from the drawings themselves, all converted to millimetres. */
function Geometry({ g }: { g: NonNullable<Result['geometry']> }) {
  const sides = [
    { title: 'Hole sizes only on one side', data: g.circle_diameters_mm },
    { title: 'Dimensions only on one side', data: g.dimensions_mm },
  ].filter((x) => x.data && (x.data.only_on_first.length || x.data.only_on_second.length));
  return (
    <section className="grid gap-3 rounded-lg border bg-card p-3">
      <h3 className="text-sm font-semibold">What’s in the files (all sizes in millimetres)</h3>
      {g.notes && g.notes.length > 0 && (
        <ul className="grid gap-0.5 text-xs text-muted-foreground">
          {g.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
      {g.size_mm && (
        <div className={cn('grid gap-1 rounded-md p-2 text-[13px] sm:grid-cols-2', g.size_mm.changed ? 'bg-warn-soft' : 'bg-muted/40')}>
          <span>
            <span className="text-muted-foreground">First overall: </span>
            {size(g.size_mm.first)}
          </span>
          <span>
            <span className="text-muted-foreground">Second overall: </span>
            {size(g.size_mm.second)}
          </span>
          {g.size_mm.changed && <span className="font-medium text-warn sm:col-span-2">The overall size is different.</span>}
        </div>
      )}
      {sides.map(({ title, data }) => (
        <div key={title} className="grid gap-1 text-[13px]">
          <p className="text-xs font-medium text-muted-foreground">{title}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <p>
              <span className="text-muted-foreground">Only on the first: </span>
              {data!.only_on_first.length ? data!.only_on_first.map(mm).join(', ') : 'none'}
            </p>
            <p>
              <span className="text-muted-foreground">Only on the second: </span>
              {data!.only_on_second.length ? data!.only_on_second.map(mm).join(', ') : 'none'}
            </p>
          </div>
        </div>
      ))}
    </section>
  );
}
