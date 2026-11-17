'use client';

import { useState } from 'react';
import { Download, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

const FORMATS = [
  ['xlsx', 'Excel'],
  ['csv', 'CSV'],
  ['pdf', 'PDF (a printable table of the first columns)'],
] as const;

/** Saves a file the server built, under the name the server chose. */
async function save(path: string, format: string) {
  const res = await authFetch(`/api/v1/exports/${path}?format=${format}`, { cache: 'no-store' });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `The download didn’t work (${res.status}).`);
  }
  const name = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(res.headers.get('content-disposition') || '')?.[1];
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = name ? decodeURIComponent(name) : `export.${format}`;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * "Download" with Excel, CSV and PDF. `list` is a list id (orders, rfqs, …) or `boards/<id>` for a monday board.
 * Read-only: it only saves a copy of what the screen shows.
 */
export function DownloadMenu({ list, className }: { list: string; className?: string }) {
  const [busy, setBusy] = useState(false);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" size="sm" className={cn(className)} disabled={busy} aria-label="Download">
            {busy ? <Loader2 className="animate-spin" /> : <Download />} <span className="max-xl:sr-only">Download</span>
          </Button>
        }
      />
      <DropdownMenuContent align="end">
        {FORMATS.map(([format, label]) => (
          <DropdownMenuItem
            key={format}
            onClick={() => {
              setBusy(true);
              save(list, format)
                .catch((e: Error) => toast.error('Couldn’t download', { description: e.message }))
                .finally(() => setBusy(false));
            }}
          >
            {label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
