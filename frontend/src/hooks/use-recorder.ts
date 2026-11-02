'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/** What the browser can record, best first: Chrome/Android/Firefox make webm or ogg, iPhone Safari mp4. */
const TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
const EXT: Record<string, string> = { 'audio/webm': '.webm', 'audio/ogg': '.ogg', 'audio/mp4': '.m4a' };

export type Recording = { blob: Blob; url: string; seconds: number; filename: string };
export type RecorderState = 'idle' | 'starting' | 'recording' | 'done' | 'error';

export const canRecord = () =>
  typeof window !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia) && typeof MediaRecorder !== 'undefined';

/**
 * Records from the microphone, on phone or desktop. `start` asks for the microphone the first
 * time; `stop` ends it and gives a playable recording with a filename the server recognises.
 * Recording stops on its own after `maxSeconds`.
 */
export function useRecorder({ maxSeconds = 300 }: { maxSeconds?: number } = {}) {
  const [state, setState] = useState<RecorderState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [recording, setRecording] = useState<Recording | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const started = useRef(0);
  const tick = useRef<ReturnType<typeof setInterval>>(undefined);
  const done = useRef<((r: Recording | null) => void) | null>(null);

  const release = useCallback(() => {
    clearInterval(tick.current);
    rec.current?.stream.getTracks().forEach((t) => t.stop());
    rec.current = null;
  }, []);

  const start = useCallback(async () => {
    if (!canRecord()) {
      // Browsers only allow the microphone on https (or localhost); a phone on the office
      // network at a plain http:// address gets no microphone at all.
      setError(
        window.isSecureContext
          ? 'This browser can’t record. Try Chrome, Edge or Safari.'
          : 'Recording needs a secure (https) address. Open the app at its https address to use the microphone.',
      );
      setState('error');
      return;
    }
    setError(null);
    setState('starting');
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError('The microphone wasn’t allowed. Allow it in the browser’s site settings and try again.');
      setState('error');
      return;
    }
    const mimeType = TYPES.find((t) => MediaRecorder.isTypeSupported(t));
    const r = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    r.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    r.onstop = () => {
      const type = (r.mimeType || mimeType || 'audio/webm').split(';')[0];
      const blob = new Blob(chunks, { type });
      const seconds = Math.max(1, Math.round((Date.now() - started.current) / 1000));
      const made = blob.size ? { blob, url: URL.createObjectURL(blob), seconds, filename: `voice${EXT[type] ?? '.webm'}` } : null;
      setRecording((old) => {
        if (old) URL.revokeObjectURL(old.url);
        return made;
      });
      setState(made ? 'done' : 'idle');
      done.current?.(made);
      done.current = null;
      release();
    };
    rec.current = r;
    started.current = Date.now();
    setElapsed(0);
    r.start(250);
    setState('recording');
    tick.current = setInterval(() => {
      const s = Math.floor((Date.now() - started.current) / 1000);
      setElapsed(s);
      if (s >= maxSeconds) r.state === 'recording' && r.stop();
    }, 250);
  }, [maxSeconds, release]);

  /** Ends the recording; resolves with it (or null when nothing was caught). */
  const stop = useCallback(
    () =>
      new Promise<Recording | null>((resolve) => {
        const r = rec.current;
        if (!r || r.state !== 'recording') return resolve(null);
        done.current = resolve;
        r.stop();
      }),
    [],
  );

  const clear = useCallback(() => {
    setRecording((old) => {
      if (old) URL.revokeObjectURL(old.url);
      return null;
    });
    setState('idle');
  }, []);

  // Leaving the page mid-recording turns the microphone off.
  useEffect(() => release, [release]);

  return { state, error, elapsed, recording, start, stop, clear };
}

export const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
