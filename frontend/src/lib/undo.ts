'use client';

import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';

const DELAY_MS = 5000;

type Pending = {
  id: string;
  timer: ReturnType<typeof setTimeout>;
  commit: (keepalive: boolean) => Promise<unknown>;
  onUndo: () => void;
  onError: (error: unknown) => void;
  /** Set for anything that goes outside Allied: leaving the page drops it (it stays waiting) instead of sending it. */
  dropOnLeave?: boolean;
};

/**
 * Undo by waiting: the change is shown at once, but only sent after five
 * seconds, so Undo never needs a reverse call the API does not have.
 * Anything still waiting is sent when the page unmounts, and on tab close with
 * `keepalive`, so leaving early never drops a decision.
 */
export function useUndoQueue(opts: { dropOnLeave?: boolean } = {}) {
  const dropOnLeave = Boolean(opts.dropOnLeave);
  const pending = useRef(new Map<string, Pending>());

  const send = useCallback(async (p: Pending, keepalive: boolean) => {
    pending.current.delete(p.id);
    clearTimeout(p.timer);
    try {
      await p.commit(keepalive);
    } catch (error) {
      p.onError(error);
    }
  }, []);

  useEffect(() => {
    const queue = pending.current;
    // Leaving the page normally sends what is waiting. For something that goes outside Allied (an email) it is dropped instead: the item
    // is still waiting for approval next time, so nothing is sent while its Undo notice is on screen.
    const flush = () => queue.forEach((p) => !p.dropOnLeave && !dropOnLeave && void send(p, true));
    window.addEventListener('pagehide', flush);
    return () => {
      window.removeEventListener('pagehide', flush);
      queue.forEach((p) => {
        if (dropOnLeave || p.dropOnLeave) {
          clearTimeout(p.timer);
          toast.dismiss(p.id);
          p.onUndo();
        } else void send(p, false);
      });
      queue.clear();
    };
  }, [send, dropOnLeave]);

  /** Shows `message` with Undo; runs `commit` after the delay unless undone. */
  return useCallback(
    (
      message: string,
      {
        commit,
        onUndo,
        onError,
        dropOnLeave: dropThis,
      }: {
        commit: (keepalive: boolean) => Promise<unknown>;
        onUndo: () => void;
        onError: (error: unknown) => void;
        dropOnLeave?: boolean;
      },
    ) => {
      const id = `${Date.now()}-${Math.random()}`;
      const entry: Pending = {
        id,
        commit,
        onUndo,
        onError,
        dropOnLeave: dropThis,
        timer: setTimeout(() => {
          const p = pending.current.get(id);
          if (p) void send(p, false);
        }, DELAY_MS),
      };
      pending.current.set(id, entry);
      toast(message, {
        id,
        duration: DELAY_MS,
        action: {
          label: 'Undo',
          onClick: () => {
            const p = pending.current.get(id);
            if (!p) return;
            clearTimeout(p.timer);
            pending.current.delete(id);
            p.onUndo();
          },
        },
      });
    },
    [send],
  );
}
