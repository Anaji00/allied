'use client';

// The floating "Ask" button. It opens the assistant chat in a panel on whatever page you are on.
// Other places (Ctrl+K) open it with a question ready by sending an "allied:ask" event.
// The chat stays mounted while the panel is closed, so the conversation is still there on reopen.

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Maximize2, Minimize2, Sparkles, Wrench, X } from 'lucide-react';
import { AssistantChat } from '@/components/assistant/assistant-chat';
import { useMondayStatus } from '@/components/admin/monday-changes';
import { useAuth } from '@/context/AuthContext';

const ICON_BUTTON =
  'grid size-8 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring outline-none';

export default function AssistantWidget() {
  const { isAuthenticated } = useAuth() || {};
  const staged = useMondayStatus().data?.staged ?? 0;
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(false);
  // A new key starts the chat afresh with the question typed in Ctrl+K.
  const [ask, setAsk] = useState({ key: 0, prompt: '' });

  useEffect(() => {
    const onAsk = (e) => {
      setAsk((a) => ({ key: a.key + 1, prompt: e.detail?.prompt || '' }));
      setOpen(true);
    };
    window.addEventListener('allied:ask', onAsk);
    return () => window.removeEventListener('allied:ask', onAsk);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    // Escape leaves full screen first, then closes the panel.
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (full) setFull(false);
      else setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, full]);

  if (!isAuthenticated) return null;

  const close = () => {
    setOpen(false);
    setFull(false);
  };

  return (
    <>
      <section
        aria-label="Assistant"
        hidden={!open}
        className={
          full
            ? 'fixed inset-0 z-50 flex flex-col bg-background duration-200 animate-in fade-in zoom-in-98'
            : 'fixed right-3 bottom-[calc(8.5rem+env(safe-area-inset-bottom))] left-3 z-40 flex h-[min(40rem,calc(100svh-12rem-env(safe-area-inset-bottom)))] origin-bottom-right flex-col overflow-hidden rounded-3xl border border-border/70 bg-background shadow-[0_24px_64px_-16px_rgba(0,0,0,0.45)] ring-1 ring-black/5 duration-200 animate-in fade-in slide-in-from-bottom-3 zoom-in-95 md:right-5 md:bottom-24 md:left-auto md:h-[min(42rem,calc(100svh-8rem))] md:w-[27rem]'
        }
      >
        <header
          className={`flex items-center gap-3 border-b border-border/60 bg-gradient-to-b from-muted/50 to-transparent py-3 ${full ? 'px-[max(1rem,calc((100%-48rem)/2))]' : 'px-4'}`}
        >
          <span className="grid size-9 place-items-center rounded-full bg-primary text-primary-foreground shadow-sm">
            <Sparkles className="size-4" />
          </span>
          <div className="grid min-w-0 flex-1 leading-tight">
            <strong className="text-sm font-semibold">Assistant</strong>
            <span className="truncate text-xs text-muted-foreground">Asks before it acts</span>
          </div>
          {staged > 0 && (
            <Link
              href="/health?tab=changes"
              onClick={close}
              className="rounded-full bg-info-soft px-2.5 py-1 text-xs text-info hover:underline"
              title="Changes waiting for monday. Nothing is sent until someone sends them."
            >
              {staged} waiting for monday
            </Link>
          )}
          <Link
            href="/chat"
            onClick={close}
            className={ICON_BUTTON}
            aria-label="Other tools: read a document, translate, email"
            title="Other tools"
          >
            <Wrench className="size-4" />
          </Link>
          <button
            type="button"
            className={ICON_BUTTON}
            aria-label={full ? 'Back to the small window' : 'Make it full screen'}
            title={full ? 'Smaller' : 'Full screen'}
            onClick={() => setFull((f) => !f)}
          >
            {full ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
          </button>
          <button type="button" className={ICON_BUTTON} aria-label="Close the assistant" title="Close" onClick={close}>
            <X className="size-4" />
          </button>
        </header>
        <div className={full ? 'min-h-0 flex-1 px-4 pt-4 pb-5 md:px-6 md:pb-8' : 'min-h-0 flex-1 px-3 pt-3 pb-3'}>
          <AssistantChat key={ask.key} initialPrompt={ask.prompt} fill />
        </div>
      </section>
      {!(open && full) && (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? 'Close the assistant' : 'Ask the assistant'}
          className="fixed right-4 bottom-[calc(5.25rem+env(safe-area-inset-bottom))] z-40 inline-flex h-12 items-center justify-center gap-2 rounded-full bg-primary px-5 max-md:size-12 max-md:px-0 text-sm font-medium text-primary-foreground shadow-[0_10px_30px_-8px_rgba(0,0,0,0.5)] outline-none transition-all hover:-translate-y-0.5 hover:shadow-[0_14px_34px_-8px_rgba(0,0,0,0.55)] focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 active:translate-y-0 md:right-5 md:bottom-5"
        >
          {open ? <X className="size-4" /> : <Sparkles className="size-4" />}
          <span className="max-md:sr-only">{open ? 'Close' : 'Ask'}</span>
        </button>
      )}
    </>
  );
}
