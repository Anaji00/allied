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
const SIMPLE = new Set(['status', 'color', 'text', 'long_text', 'long-text', 'numbers', 'dropdown', 'date']);

type Dragged = { type: string; value: string; from: string };
// What is being dragged right now, so only cells that can take it light up while the pointer is over them.
let current: Dragged | null = null;
const fits = (from: string, to: string) => from === to || (from === 'text' && to === 'long_text');

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
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const can = hasRole('OPERATOR');

  function save(item: Item, col: Column, value: string) {
    undo(`${col.title} on ${item.name} will become “${value}”`, {
      commit: async (keepalive) => {
        const res = await authFetch(`/api/v1/boards/${encodeURIComponent(board.board_id)}/items/${encodeURIComponent(item.id)}/changes`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ columns: { [col.id]: value } }),
          keepalive,
        });
        if (!res.ok) throw new Error(await detail(res));
        toast.success('Saved, waiting to go to monday', { description: `${item.name}: ${col.title} → ${value}` });
        void client.invalidateQueries({ queryKey: ['monday-push'] });
      },
      onUndo: () => undefined,
      onError: (e) => toast.error('That wasn’t saved', { description: e instanceof Error ? e.message : undefined }),
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
    if (
      (col.type === 'status' || col.type === 'color' || col.type === 'dropdown') &&
      col.labels &&
      !Object.keys(col.labels).includes(text)
    ) {
      toast.warning(`“${text}” isn’t one of the choices in ${col.title}`);
      return;
    }
    if (col.type === 'numbers' && !/^-?\d+(\.\d+)?$/.test(text)) {
      toast.warning('This column takes a number');
      return;
    }
    save(item, col, text);
  }

  function dragProps(item: Item, col: Column, value: string) {
    if (!can) return {};
    // People are picked in the item panel: monday works those out itself.
    if (col.type === 'people' || col.type === 'person' || col.type === 'multiple-person') {
      return {
        onDragOver: (e: DragEvent) => e.preventDefault(),
        onDrop: (e: DragEvent) => {
          e.preventDefault();
          toast.info('Pick people in the item panel', { description: 'Open the row and use “Set the owner”.' });
        },
      };
    }
    if (!SIMPLE.has(col.type)) return {};
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

  return { over, dragProps, can, held, cancel: () => setHeld(null) };
}
