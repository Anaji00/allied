'use client';

import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Download, FileText, Loader2, Paperclip, X } from 'lucide-react';
import { PanelHeading } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { ItemType } from './updates-panel';

export type ItemFile = {
  id: number;
  item_type?: string;
  item_ref?: string;
  update_id?: number | null;
  filename: string;
  size: number;
  content_type?: string;
  uploaded_by?: string;
  uploaded_by_name?: string;
  created_at?: string;
  duplicate?: boolean;
  /** A design file: only these are attached to a vendor's price request or purchase order. */
  design?: boolean;
};
type Allowed = { extensions: string[]; max_mb: number; max_per_item: number };

const BASE = '/api/v1/files';

async function detail(res: Response, fallback: string) {
  const d = await res
    .json()
    .then((j) => j?.detail)
    .catch(() => null);
  return new Error(typeof d === 'string' ? d : fallback);
}

export const fileSize = (n: number) =>
  n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;

/** A protected file, fetched with the sign-in and saved under its own name (a plain link would arrive without the sign-in). */
export async function downloadFile(id: number | string, filename?: string) {
  const res = await authFetch(`${BASE}/${id}/download`, { cache: 'no-store' });
  if (!res.ok) throw await detail(res, `Couldn’t download it (${res.status}).`);
  const named = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(res.headers.get('content-disposition') || '')?.[1];
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || (named ? decodeURIComponent(named) : 'file');
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Attach files to an item, or to one comment on it. */
export async function uploadFiles(itemType: ItemType, itemRef: string, files: File[], updateId?: number): Promise<ItemFile[]> {
  const form = new FormData();
  form.append('item_type', itemType);
  form.append('item_ref', itemRef);
  if (updateId != null) form.append('update_id', String(updateId));
  files.forEach((f) => form.append('upload', f, f.name));
  const res = await authFetch(BASE, { method: 'POST', body: form });
  if (!res.ok) throw await detail(res, `The files weren’t attached (${res.status}).`);
  return (await res.json()) as ItemFile[];
}

/** What can be attached: types and sizes, said once in the picker's help. */
export function useAllowedFiles() {
  return useQuery({
    queryKey: ['files', 'allowed'],
    queryFn: async (): Promise<Allowed | null> => {
      const res = await authFetch(`${BASE}/allowed`, { cache: 'no-store' });
      return res.ok ? ((await res.json()) as Allowed) : null;
    },
    staleTime: 10 * 60_000,
    retry: false,
  });
}
export const allowedHelp = (a?: Allowed | null) =>
  a ? `Up to ${a.max_per_item} files, ${a.max_mb} MB each. ${a.extensions.map((e) => e.replace(/^\./, '')).join(', ')}.` : '';

/** One file: its name and size, and a tap to download it. */
export function FileChip({ file, onRemove }: { file: ItemFile; onRemove?: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <span className="inline-flex max-w-full items-center gap-1 rounded-full border bg-card py-0.5 pr-1 pl-2.5 text-xs">
      <FileText className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
      <button
        type="button"
        disabled={busy}
        className="min-w-0 truncate hover:underline"
        title={`Download ${file.filename}`}
        onClick={() => {
          setBusy(true);
          downloadFile(file.id, file.filename)
            .catch((e: Error) => toast.error('Couldn’t download it', { description: e.message }))
            .finally(() => setBusy(false));
        }}
      >
        {file.filename}
      </button>
      <span className="shrink-0 text-muted-foreground tabular-nums">{fileSize(file.size)}</span>
      {busy ? (
        <Loader2 className="size-3 animate-spin" />
      ) : (
        <Download className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
      )}
      {onRemove && (
        <button
          type="button"
          aria-label={`Remove ${file.filename}`}
          className="grid size-5 place-items-center rounded-full hover:bg-muted hover:text-bad"
          onClick={onRemove}
        >
          <X className="size-3" />
        </button>
      )}
    </span>
  );
}

/** Every file on one item, with a place to drop or choose more. Says nothing until the server has file sharing. */
export function FilesStrip({ itemType, itemRef }: { itemType: ItemType; itemRef: string }) {
  const { hasRole, employeeEmail } = useAuth();
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const allowed = useAllowedFiles();
  const key = ['files', itemType, itemRef];
  const list = useQuery({
    queryKey: key,
    queryFn: async (): Promise<ItemFile[] | null> => {
      const res = await authFetch(`${BASE}?item_type=${itemType}&item_ref=${encodeURIComponent(itemRef)}`, { cache: 'no-store' });
      if (res.status === 404 || res.status >= 500) return null;
      if (!res.ok) throw await detail(res, `Couldn’t load the files (${res.status}).`);
      return (await res.json()) as ItemFile[];
    },
    retry: false,
  });
  const add = useMutation({
    mutationFn: (files: File[]) => uploadFiles(itemType, itemRef, files),
    onSuccess: (r) => {
      toast.success(r.length === 1 ? 'File added' : `${r.length} files added`);
      void client.invalidateQueries({ queryKey: ['files'] });
    },
    onError: (e) => toast.error('The files weren’t added', { description: e.message }),
  });
  const remove = useMutation({
    mutationFn: async (id: number) => {
      const res = await authFetch(`${BASE}/${id}`, { method: 'DELETE' });
      if (!res.ok) throw await detail(res, `Couldn’t remove it (${res.status}).`);
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['files'] }),
    onError: (e) => toast.error('That couldn’t be removed', { description: e.message }),
  });

  const mark = useMutation({
    mutationFn: async ({ id, design }: { id: number; design: boolean }) => {
      const res = await authFetch(`${BASE}/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design }),
      });
      if (!res.ok) throw await detail(res, `That didn’t save (${res.status}).`);
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['files'] }),
    onError: (e) => toast.error('That didn’t save', { description: e.message }),
  });

  if (list.isPending || list.isError || list.data == null) return null;
  const files = list.data;
  const canWrite = hasRole('OPERATOR');
  const take = (picked: FileList | null) => {
    const f = Array.from(picked ?? []);
    if (f.length) add.mutate(f);
    if (input.current) input.current.value = '';
  };

  return (
    <div
      className={cn('grid gap-2 rounded-md border p-3 transition-colors', over && 'border-foreground bg-muted/40')}
      onDragOver={(e) => {
        if (!canWrite) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        if (!canWrite) return;
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files);
      }}
    >
      <div className="flex items-center gap-2">
        <PanelHeading>
          <span className="inline-flex items-center gap-1.5">
            <Paperclip className="size-3.5" /> Files{files.length > 0 && ` (${files.length})`}
          </span>
        </PanelHeading>
        {canWrite && (
          <>
            <Button
              type="button"
              size="xs"
              variant="outline"
              className="ml-auto"
              disabled={add.isPending}
              onClick={() => input.current?.click()}
            >
              {add.isPending && <Loader2 className="animate-spin" />}
              Add files
            </Button>
            <input ref={input} type="file" multiple className="sr-only" aria-label="Add files" onChange={(e) => take(e.target.files)} />
          </>
        )}
      </div>
      {files.length === 0 ? (
        <p className="text-sm text-muted-foreground">No files yet.{canWrite && ' Drop some here, or press Add files.'}</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {files.map((f) => (
            <span key={f.id} className="inline-flex max-w-full flex-wrap items-center gap-1.5">
              <FileChip
                file={f}
                onRemove={
                  f.uploaded_by === employeeEmail || hasRole('MANAGER')
                    ? () => {
                        if (window.confirm(`Remove ${f.filename}?`)) remove.mutate(f.id);
                      }
                    : undefined
                }
              />
              {canWrite && f.design !== undefined && (
                <label className="inline-flex items-center gap-1 text-xs text-muted-foreground" title="Only design files go to vendors">
                  <input
                    type="checkbox"
                    className="size-3.5 accent-foreground"
                    checked={f.design}
                    disabled={mark.isPending}
                    onChange={(e) => mark.mutate({ id: f.id, design: e.target.checked })}
                  />
                  Send to vendors
                </label>
              )}
            </span>
          ))}
        </div>
      )}
      {canWrite && allowed.data && <p className="text-xs text-muted-foreground">{allowedHelp(allowed.data)}</p>}
    </div>
  );
}
