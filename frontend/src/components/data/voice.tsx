'use client';

import { useRef, useState } from 'react';
import { Loader2, Mic, Square } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { clock, useRecorder, type Recording } from '@/hooks/use-recorder';
import { authFetch } from '@/lib/api';
import { cn } from '@/lib/utils';

// Held longer than this, letting go stops the recording; a quick tap starts it and the next tap stops it.
const HOLD_MS = 400;

type Recorder = ReturnType<typeof useRecorder>;

/** Sends a form (a file and fields) with the sign-in header; the server's own words on failure. */
export async function postForm<T>(path: string, form: FormData): Promise<T> {
  const res = await authFetch(`/api/v1${path}`, { method: 'POST', body: form });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((j) => j?.detail)
      .catch(() => null);
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status}).`);
  }
  return res.json() as Promise<T>;
}

/** Speech to text through the app's transcriber. */
export async function transcribe(rec: Recording) {
  const form = new FormData();
  form.append('file', rec.blob, rec.filename);
  const out = await postForm<{ text?: string }>('/ai/transcribe', form);
  return (out.text ?? '').trim();
}

/**
 * A microphone button for phone and desktop: hold to record and let go to stop, or tap to
 * start and tap again to stop. Enter or Space toggles it from the keyboard.
 */
export function MicButton({
  recorder,
  label,
  onStopped,
  className,
  size = 'sm',
}: {
  recorder: Recorder;
  label: string;
  onStopped?: (rec: Recording | null) => void;
  className?: string;
  size?: 'sm' | 'xs' | 'icon-sm';
}) {
  const pressed = useRef(0);
  const live = recorder.state === 'recording';
  const finish = () => void recorder.stop().then((r) => onStopped?.(r));

  return (
    <Button
      type="button"
      size={size}
      variant={live ? 'destructive' : 'outline'}
      aria-pressed={live}
      aria-label={live ? `Stop recording (${clock(recorder.elapsed)})` : label}
      title={live ? 'Let go, or tap, to stop' : `${label}: hold to record, or tap to start`}
      className={cn('touch-none select-none', className)}
      disabled={recorder.state === 'starting'}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        if (live) return finish();
        pressed.current = Date.now();
        void recorder.start();
      }}
      onPointerUp={() => {
        if (live && pressed.current && Date.now() - pressed.current > HOLD_MS) finish();
        pressed.current = 0;
      }}
      onClick={(e) => {
        // Keyboard activation only (a pointer was handled above).
        if (e.detail !== 0) return;
        if (live) finish();
        else void recorder.start();
      }}
    >
      {recorder.state === 'starting' ? <Loader2 className="animate-spin" /> : live ? <Square /> : <Mic />}
      {size !== 'icon-sm' && (live ? `Stop · ${clock(recorder.elapsed)}` : label)}
    </Button>
  );
}

/** A microphone beside a text box: speak, and the words are written into the box. */
export function DictateButton({
  onText,
  className,
  compact = false,
}: {
  onText: (text: string) => void;
  className?: string;
  compact?: boolean;
}) {
  const recorder = useRecorder({ maxSeconds: 180 });
  const [busy, setBusy] = useState(false);

  if (busy)
    return (
      <Button type="button" size={compact ? 'icon-sm' : 'xs'} variant="outline" disabled className={className} aria-label="Writing it out">
        <Loader2 className="animate-spin" /> {!compact && 'Writing it out…'}
      </Button>
    );
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <MicButton
        recorder={recorder}
        size={compact ? 'icon-sm' : 'xs'}
        label="Dictate"
        onStopped={(rec) => {
          if (!rec) return;
          setBusy(true);
          transcribe(rec)
            .then((text) => (text ? onText(text) : toast('Nothing was heard. Try again a little closer to the microphone.')))
            .catch((e: Error) => toast.error('It couldn’t be written out', { description: e.message }))
            .finally(() => {
              setBusy(false);
              recorder.clear();
            });
        }}
      />
      {recorder.state === 'error' && <span className="text-xs text-bad">{recorder.error}</span>}
    </span>
  );
}

/** Joins dictated text onto what's already typed. */
export const addDictation = (current: string, text: string) => (current.trim() ? `${current.trimEnd()} ${text}` : text);
