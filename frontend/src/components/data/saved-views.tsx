'use client';

import { useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Bookmark, Loader2, Trash2, Pin } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';

type View = {
  id: number | string;
  name: string;
  screen: string;
  filters: { query?: string };
  shared?: boolean;
  mine?: boolean;
  pinned?: boolean;
};

const BASE = '/api/v1/workspace/views';

async function send(path: string, method: string, body?: unknown) {
  const res = await authFetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `That didn’t save (${res.status}).`);
  }
}

/**
 * "Views": go straight to a filter you use a lot. A view is the screen's address-bar filters
 * (what is typed, ticked or picked), so it works on every screen that keeps its filters in the address.
 * Hidden until the server has saved views.
 */
export function SavedViews({ screen, className }: { screen: string; className?: string }) {
  const { hasRole } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const client = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState('');
  const [shared, setShared] = useState(false);

  const views = useQuery({
    queryKey: ['views', screen],
    queryFn: async (): Promise<View[] | null> => {
      const res = await authFetch(`${BASE}?screen=${encodeURIComponent(screen)}`, { cache: 'no-store' });
      return res.ok ? ((await res.json()) as View[]) : null;
    },
    retry: false,
  });
  const save = useMutation({
    mutationFn: () => send('', 'POST', { name: name.trim(), screen, filters: { query: params.toString() }, shared: shared || undefined }),
    onSuccess: () => {
      toast.success('View saved');
      setSaving(false);
      setName('');
      setShared(false);
      void client.invalidateQueries({ queryKey: ['views', screen] });
    },
    onError: (e) => toast.error('The view wasn’t saved', { description: e.message }),
  });
  const pin = useMutation({
    mutationFn: ({ id, on }: { id: View['id']; on: boolean }) => send(`/${id}/pin`, on ? 'POST' : 'DELETE'),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['views', screen] }),
    onError: (e) => toast.error('That couldn’t be pinned', { description: e.message }),
  });
  const remove = useMutation({
    mutationFn: (id: View['id']) => send(`/${id}`, 'DELETE'),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['views', screen] }),
    onError: (e) => toast.error('That couldn’t be removed', { description: e.message }),
  });

  if (!views.data) return null;
  const go = (v: View) => router.push(v.filters?.query ? `${pathname}?${v.filters.query}` : pathname);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="sm" className={className} aria-label="Views" />}>
          <Bookmark /> <span className="max-xl:sr-only">Views</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          {views.data.length === 0 ? (
            <DropdownMenuLabel className="font-normal text-muted-foreground">No saved views yet.</DropdownMenuLabel>
          ) : (
            views.data.map((v) => (
              <DropdownMenuItem key={v.id} onClick={() => go(v)} className="gap-2">
                <span className="min-w-0 flex-1 truncate">{v.name}</span>
                {v.shared && <span className="text-xs text-muted-foreground">Shared</span>}
                {v.pinned !== undefined && (
                  <button
                    type="button"
                    aria-label={v.pinned ? `Unpin ${v.name}` : `Pin ${v.name} to the top`}
                    aria-pressed={v.pinned}
                    className={`grid size-6 place-items-center rounded-md hover:bg-muted ${v.pinned ? 'text-foreground' : 'text-muted-foreground'}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      pin.mutate({ id: v.id, on: !v.pinned });
                    }}
                  >
                    <Pin className={`size-3.5 ${v.pinned ? 'fill-current' : ''}`} />
                  </button>
                )}
                {v.mine && (
                  <button
                    type="button"
                    aria-label={`Delete ${v.name}`}
                    className="grid size-6 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-bad"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (window.confirm(`Delete the view “${v.name}”?`)) remove.mutate(v.id);
                    }}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                )}
              </DropdownMenuItem>
            ))
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setSaving(true)}>Save this view…</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={saving} onOpenChange={setSaving}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Save this view</DialogTitle>
            <DialogDescription>It keeps what is filtered, searched and picked on this screen right now.</DialogDescription>
          </DialogHeader>
          <form
            id="save-view"
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) save.mutate();
            }}
          >
            <Input
              autoFocus
              placeholder="A name, like “Late ZIBO orders”"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={60}
            />
            {hasRole('MANAGER') && (
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="size-4" checked={shared} onChange={(e) => setShared(e.target.checked)} />
                Share it with everyone
              </label>
            )}
          </form>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setSaving(false)}>
              Cancel
            </Button>
            {!name.trim() && <span className="mr-auto text-xs text-muted-foreground">Name the view to save it.</span>}
            <Button type="submit" form="save-view" disabled={!name.trim() || save.isPending}>
              {save.isPending && <Loader2 className="animate-spin" />}
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
