'use client';

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { authFetch } from '@/lib/api';

type Cell = { row: number; col: number; item: string; column: string; text: string; understood?: string; was?: string; becomes?: string };
type Problem = { row: number; col: number; item?: string; column?: string; text: string; problem: string };
type Result = { staged?: boolean; cells: Cell[]; problems: Problem[]; rows_touched?: number; message?: string };

/**
 * Copy a block of cells in Excel, click a cell on the board, press Ctrl+V. The block is read into the board from that cell
 * down and across. A preview says what would change first; it is all or nothing, and saved only as changes waiting for monday.
 */
export function PasteBlock({ boardId, active }: { boardId: string; active: { itemId: string; columnId: string } | null }) {
  const client = useQueryClient();
  const [tsv, setTsv] = useState('');
  const [preview, setPreview] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const url = `/api/v1/board-edit/boards/${encodeURIComponent(boardId)}/paste`;

  async function call(dry: boolean, text: string): Promise<Result> {
    const res = await authFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ top_left: { item_id: active!.itemId, column: active!.columnId }, tsv: text, dry_run: dry }),
    });
    const body = (await res.json().catch(() => null)) as (Result & { detail?: string }) | null;
    if (!res.ok || !body) throw new Error(typeof body?.detail === 'string' ? body.detail : `That didn’t work (${res.status}).`);
    return body;
  }

  useEffect(() => {
    if (!active) return undefined;
    const onPaste = (e: ClipboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      const text = e.clipboardData?.getData('text/plain') ?? '';
      // A single value is just a value; a block (tabs or several lines) is what this is for.
      if (!/[\t\n]/.test(text.trim())) return;
      e.preventDefault();
      setTsv(text);
      setBusy(true);
      call(true, text)
        .then(setPreview)
        .catch((err: Error) => toast.error('Couldn’t read what you copied', { description: err.message }))
        .finally(() => setBusy(false));
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.itemId, active?.columnId, boardId]);

  const close = () => {
    setPreview(null);
    setTsv('');
  };
  async function save() {
    setBusy(true);
    try {
      const r = await call(false, tsv);
      toast.success('Saved, waiting to go to monday', { description: r.message ?? `${r.cells.length} cells` });
      void client.invalidateQueries({ queryKey: ['monday-push'] });
      close();
    } catch (e) {
      toast.error('Nothing was saved', { description: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(false);
    }
  }

  const problems = preview?.problems ?? [];
  return (
    <Dialog open={Boolean(preview)} onOpenChange={(o) => !o && close()}>
      <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Paste {preview?.cells.length ?? 0} cells?</DialogTitle>
          <DialogDescription>
            {problems.length > 0
              ? 'Something in what you copied doesn’t fit, so nothing will be saved. Fix it and copy again.'
              : 'This is what would change. Nothing goes to monday until a manager sends the changes.'}
          </DialogDescription>
        </DialogHeader>
        {problems.length > 0 ? (
          <ul className="grid gap-1 text-[13px]">
            {problems.slice(0, 12).map((p, i) => (
              <li key={i}>
                <span className="font-medium">
                  {p.item ?? `Row ${p.row + 1}`}, {p.column ?? `column ${p.col + 1}`}:
                </span>{' '}
                <span className="text-warn">{p.problem}</span>
              </li>
            ))}
          </ul>
        ) : (
          <ul className="grid max-h-64 gap-1 overflow-y-auto text-[13px]">
            {(preview?.cells ?? []).slice(0, 40).map((c, i) => (
              <li key={i}>
                <span className="font-medium">
                  {c.item}, {c.column}:
                </span>{' '}
                <span className="text-muted-foreground line-through">{c.was || '(blank)'}</span>{' '}
                <span className="text-ok">{c.understood || c.becomes || c.text}</span>
              </li>
            ))}
            {(preview?.cells.length ?? 0) > 40 && (
              <li className="text-xs text-muted-foreground">and {(preview?.cells.length ?? 0) - 40} more.</li>
            )}
          </ul>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button disabled={busy || problems.length > 0 || (preview?.cells.length ?? 0) === 0} onClick={() => void save()}>
            {busy && <Loader2 className="animate-spin" />}
            Save these {preview?.cells.length ?? 0}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
