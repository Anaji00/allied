'use client';

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api, ok } from '@/lib/api/client';

/** Tracked boards are projects, vendors and rfqs; reference boards and monday Docs are shown but not tracked. */
export type Role = 'projects' | 'vendors' | 'rfqs' | 'reference' | 'doc';
export const TRACKED: Role[] = ['projects', 'vendors', 'rfqs'];

export type BoardSummary = { board_id: string; name: string; role: Role; label: string; read_at: string | null; items: number };

export type Column = { id: string; title: string; type: string; width?: number | null; labels?: Record<string, string> };
export type Group = { id: string; title: string; color?: string | null };

/** What the app makes of a row; null when the app doesn't track it. */
export type AppView =
  | { state: string; days?: number | null; owners?: string[] } // projects
  | { job_id: number; state: string; expected?: string | null; need_by?: string | null; reasons?: string[] } // vendors
  | { rfq_id: number; number: string; state?: string | null; status: string; due_at?: string | null }; // rfqs

export type Item = {
  id: string;
  name: string;
  created_at?: string | null;
  group: string | null;
  group_title?: string | null;
  values: Record<string, string>;
  app: AppView | null;
};

export type Board = {
  board_id: string;
  name: string;
  label: string;
  role: Role;
  read_at: string | null;
  status_column: string;
  hide_done: boolean;
  columns: Column[];
  groups: Group[];
  items: Item[];
};

export function useBoards() {
  return useQuery({
    queryKey: ['boards'],
    queryFn: async () => (await ok(api.GET('/api/v1/boards'))) as unknown as BoardSummary[],
    refetchInterval: 5 * 60_000,
  });
}

export function useBoard(boardId: string | null, hideDone: boolean) {
  return useQuery({
    queryKey: ['boards', boardId, hideDone],
    queryFn: async () =>
      (await ok(
        api.GET('/api/v1/boards/{board_id}', { params: { path: { board_id: boardId! }, query: { hide_done: hideDone } } }),
      )) as unknown as Board,
    enabled: Boolean(boardId),
    placeholderData: keepPreviousData,
    refetchInterval: 5 * 60_000,
  });
}

/** Black or white text, whichever reads better on a monday label colour. */
export function inkOn(hex: string) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return undefined;
  const n = parseInt(m[1], 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  // White unless the colour is light enough that black clearly wins.
  return lum > 0.4 ? '#111' : '#fff';
}
