'use client';

import { useRef, useState, type DragEvent, type MouseEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';
import { useUndoQueue } from '@/lib/undo';
import type { Board, Column, Item } from './use-boards';

const MIME = 'application/x-allied-cell';
// Plain values that can be copied from one cell to another. People and formula columns are left out: monday works those out itself.
export const EDITABLE_TYPES = new Set([
  'status',
  'color',
  'text',
  'long_text',
  'long-text',
  'numbers',
  'dropdown',
  'date',
  'checkbox',
  'email',
  'phone',
  'link',
  'people',
  'person',
  'multiple-person',
]);

type Dragged = { type: string; value: string; from: string };
// What is being dragged right now, so only cells that can take it light up while the pointer is over them.
let current: Dragged | null = null;
const fits = (from: string, to: string) => from === to || (from === 'text' && to === 'long_text');

const without = (o: Record<string, string>, k: string) => Object.fromEntries(Object.entries(o).filter(([key]) => key !== k));

async function detail(res: Response) {
  const d = await res
    .json()
    .then((j) => j?.detail)
    .catch(() => null);
  return typeof d === 'string' ? d : `That didn’t work (${res.status}).`;
}

/**
 * Drag a value from one cell and drop it on another cell in the same kind of column to copy it. A status can go to any
 * status cell that has that label, and plain text from anywhere can be dropped on a text cell. The change is saved for monday
 * (never sent from here), with a few seconds to undo.
 */
export function useCellDrag(board: Board) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const undo = useUndoQueue();
  const [over, setOver] = useState<string | null>(null);
  const [held, setHeld] = useState<Dragged | null>(null);
  const [pending, setPending] = useState<Record<string, string>>({});
  // Rows just typed in, shown at once as new rows until the saved copy comes back.
  const [fresh, setFresh] = useState<{ key: string; name: string; group?: string }[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const can = hasRole('OPERATOR');

  const edit = (path: string) => `/api/v1/board-edit/boards/${encodeURIComponent(board.board_id)}${path}`;
  const json = { 'Content-Type': 'application/json' };

  function save(item: Item, col: Column, value: string) {
    if (item.id.startsWith('fresh:') || item.id.startsWith('new:s')) {
      toast.info('That row is still being saved', { description: 'Try again in a moment.' });
      return;
    }
    // Show the new value at once, as waiting, until it is saved or undone.
    setPending((p) => ({ ...p, [`${item.id}:${col.id}`]: value }));
    undo(`${col.title} on ${item.name} will become “${value}”`, {
      commit: async (keepalive) => {
        // The editing route reads the text by column type ("fri", "nov 20", "$1,200"); older servers only have the plain staging call.
        let res = await authFetch(edit(`/items/${encodeURIComponent(item.id)}/cell`), {
          method: 'POST',
          headers: json,
          body: JSON.stringify({ column: col.id, text: value }),
          keepalive,
        });
        let understood = '';
        if (res.status === 404 || res.status === 405) {
          res = await authFetch(`/api/v1/boards/${encodeURIComponent(board.board_id)}/items/${encodeURIComponent(item.id)}/changes`, {
            method: 'POST',
            headers: json,
            body: JSON.stringify({ columns: { [col.id]: value } }),
            keepalive,
          });
        } else if (res.ok) {
          understood =
            (
              (await res
                .clone()
                .json()
                .catch(() => null)) as { understood?: string } | null
            )?.understood ?? '';
        }
        if (!res.ok) throw new Error(await detail(res));
        toast.success('Saved', {
          id: 'board-saved',
          description: `${item.name}: ${col.title} → ${understood || value}. Waiting to go to monday.`,
        });
        setPending((p) => without(p, `${item.id}:${col.id}`));
        void client.invalidateQueries({ queryKey: ['monday-push'] });
        void client.invalidateQueries({ queryKey: ['boards'] });
      },
      onUndo: () => setPending((p) => without(p, `${item.id}:${col.id}`)),
      onError: (e) => {
        setPending((p) => without(p, `${item.id}:${col.id}`));
        toast.error('That wasn’t saved', { description: e instanceof Error ? e.message : undefined });
      },
    });
  }

  /** Checks that the value fits the cell, then saves it. Used by both a drop and a tap-to-paste. */
  function paste(item: Item, col: Column, from: Dragged | null, rawText: string, key: string, shownValue: string) {
    const text = (from?.value ?? rawText).trim();
    if (!text || from?.from === key || text === shownValue) return;
    if (from && !fits(from.type, col.type)) {
      toast.warning('That value doesn’t fit this column', { description: `Use another ${from.type.replace(/_/g, ' ')} cell.` });
      return;
    }
    if (col.type === 'numbers' && !/^[-+]?[$€£¥]?\s*[\d,]*\.?\d+\s*%?$/.test(text)) {
      toast.warning(`“${text}” isn’t a number`, { description: `${col.title} takes figures like 1200 or 12.5.` });
      return;
    }
    if (
      (col.type === 'status' || col.type === 'color' || col.type === 'dropdown') &&
      col.labels &&
      !Object.keys(col.labels).includes(text)
    ) {
      toast.warning(`“${text}” isn’t one of the choices in ${col.title}`);
      return;
    }
    save(item, col, text);
  }

  function dragProps(item: Item, col: Column, value: string) {
    if (!can) return {};
    // People are picked in the item panel: monday works those out itself.
    if (col.type === 'people' || col.type === 'person' || col.type === 'multiple-person') {
      return {
        onClickCapture: (e: MouseEvent) => {
          if (held) {
            e.stopPropagation();
            e.preventDefault();
            setHeld(null);
            toast.info('People can be typed in the cell, not dropped');
          }
        },
        onDragOver: (e: DragEvent) => e.preventDefault(),
        onDrop: (e: DragEvent) => {
          e.preventDefault();
          toast.info('Pick people in the item panel', { description: 'Open the row and use “Set the owner”.' });
        },
      };
    }
    if (!EDITABLE_TYPES.has(col.type)) return {};
    const key = `${item.id}:${col.id}`;
    return {
      draggable: Boolean(value),
      onDragStart: (e: DragEvent) => {
        const payload: Dragged = { type: col.type, value, from: key };
        e.dataTransfer.setData(MIME, JSON.stringify(payload));
        e.dataTransfer.setData('text/plain', value);
        e.dataTransfer.effectAllowed = 'copy';
        current = payload;
      },
      onDragEnd: () => {
        current = null;
        setOver(null);
      },
      onDragOver: (e: DragEvent) => {
        if (current && !fits(current.type, col.type)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        if (over !== key) setOver(key);
      },
      onDragLeave: () => setOver((o) => (o === key ? null : o)),
      onDrop: (e: DragEvent) => {
        e.preventDefault();
        setOver(null);
        current = null;
        const raw = e.dataTransfer.getData(MIME);
        paste(item, col, raw ? (JSON.parse(raw) as Dragged) : null, e.dataTransfer.getData('text/plain'), key, value);
      },
      // Touchscreens cannot drag: hold a cell for half a second to pick its value up, then tap another cell to paste it.
      onTouchStart: () => {
        if (!value) return;
        clearTimeout(timer.current);
        timer.current = setTimeout(() => {
          setHeld({ type: col.type, value, from: key });
          navigator.vibrate?.(30);
        }, 500);
      },
      onTouchEnd: () => clearTimeout(timer.current),
      onTouchMove: () => clearTimeout(timer.current),
      onClickCapture: (e: MouseEvent) => {
        if (!held) return;
        e.stopPropagation();
        e.preventDefault();
        const from = held;
        setHeld(null);
        paste(item, col, from, '', key, value);
      },
    };
  }

  /** A value typed into a cell: checked like a drop, then saved with the same undo. */
  function typed(item: Item, col: Column, text: string) {
    paste(item, col, null, text, `${item.id}:${col.id}`, item.values[col.id] ?? '');
  }

  /** A new row in a group, saved as a change waiting for monday (a name is enough). */
  function addRow(name: string, groupId?: string) {
    const clean = name.trim();
    if (!clean) return;
    const key = `${Date.now()}${Math.random().toString(36).slice(2, 6)}`;
    setFresh((f) => [...f, { key, name: clean, group: groupId }]);
    const drop = () => setFresh((f) => f.filter((x) => x.key !== key));
    undo(`New row “${clean}” will be added`, {
      commit: async (keepalive) => {
        let res = await authFetch(edit('/items'), {
          method: 'POST',
          headers: json,
          body: JSON.stringify({ name: clean, client_key: key, ...(groupId ? { group: groupId } : {}) }),
          keepalive,
        });
        if (res.status === 404 || res.status === 405) {
          res = await authFetch(`/api/v1/boards/${encodeURIComponent(board.board_id)}/items`, {
            method: 'POST',
            headers: json,
            body: JSON.stringify({ item_name: clean, fields: {} }),
            keepalive,
          });
        }
        if (!res.ok) throw new Error(await detail(res));
        toast.success('Saved', { id: 'board-saved', description: `New row: ${clean}. Waiting to go to monday.` });
        await Promise.all([client.invalidateQueries({ queryKey: ['monday-push'] }), client.invalidateQueries({ queryKey: ['boards'] })]);
        drop();
      },
      onUndo: drop,
      onError: (e) => {
        drop();
        toast.error('The row wasn’t saved', { description: e instanceof Error ? e.message : undefined });
      },
    });
  }

  /** Duplicate, move to a group, or archive a row: each is saved as a change waiting for monday, with the same undo. */
  function rowAction(item: Item, what: 'duplicate' | 'move' | 'archive', group?: string) {
    const words = {
      duplicate: `“${item.name}” will be copied`,
      move: `“${item.name}” will move`,
      archive: `“${item.name}” will be archived`,
    }[what];
    undo(words, {
      commit: async (keepalive) => {
        const res = await authFetch(edit(`/items/${encodeURIComponent(item.id)}/${what}`), {
          method: 'POST',
          headers: json,
          body: JSON.stringify(what === 'move' ? { group } : {}),
          keepalive,
        });
        if (!res.ok) throw new Error(await detail(res));
        toast.success('Saved', {
          id: 'board-saved',
          description:
            (
              await res
                .clone()
                .json()
                .catch(() => null)
            )?.message ?? words,
        });
        void client.invalidateQueries({ queryKey: ['monday-push'] });
        void client.invalidateQueries({ queryKey: ['boards'] });
      },
      onUndo: () => undefined,
      onError: (e) => toast.error('That wasn’t saved', { description: e instanceof Error ? e.message : undefined }),
    });
  }

  /** Put back the last thing I changed on this board (a cell, or a staged change). */
  async function undoLast() {
    const res = await authFetch('/api/v1/board-edit/undo', {
      method: 'POST',
      headers: json,
      body: JSON.stringify({ board_id: board.board_id }),
    });
    if (!res.ok) return toast.error('Nothing to undo', { description: await detail(res) });
    const out = (await res.json().catch(() => null)) as { undone?: boolean; changed_since?: boolean; message?: string } | null;
    // Someone changed the cell after this edit: it is left as it is, and the value stays on screen.
    if (out?.undone === false) return toast.info(out.message ?? 'That was left as it is.');
    toast.success(out?.message ?? 'Put back');
    void client.invalidateQueries({ queryKey: ['monday-push'] });
    void client.invalidateQueries({ queryKey: ['boards'] });
  }

  return { over, dragProps, can, held, pending, fresh, cancel: () => setHeld(null), typed, addRow, rowAction, undoLast };
}
