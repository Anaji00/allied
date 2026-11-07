'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { voiceCommand, type ChatMessage, type VoiceReply } from '@/lib/assistant-api';
import { canRecord } from '@/hooks/use-recorder';

export type VoiceState = 'off' | 'listening' | 'thinking' | 'speaking';

const TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
const EXT: Record<string, string> = { 'audio/webm': '.webm', 'audio/ogg': '.ogg', 'audio/mp4': '.m4a' };
// How loud counts as speech, how long a pause ends a sentence, how long to wait for someone to start.
const SPEECH_LEVEL = 0.035;
const PAUSE_MS = 1100;
const WAIT_MS = 9000;
const MAX_MS = 60_000;

/**
 * A voice conversation: listens, notices when you stop talking, sends it, speaks the answer,
 * and listens again while changes are waiting for a yes. Nothing is sent until you stop talking;
 * leaving voice mode turns the microphone off.
 */
export function useVoiceAgent({
  context,
  onTurn,
  onError,
}: {
  /** The chat so far and the changes waiting on a yes, read fresh at each turn. */
  context: () => { messages: ChatMessage[]; confirmIds: string[] };
  /** Called with each answer, so the page can show the words and cards. */
  onTurn: (reply: VoiceReply) => void;
  onError: (message: string) => void;
}) {
  const [state, setState] = useState<VoiceState>('off');
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState('');
  const [said, setSaid] = useState('');
  const live = useRef(false); // voice mode is on
  const stream = useRef<MediaStream | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const frame = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const turn = useRef(0); // a stale answer (after Stop or a new turn) is ignored
  const cb = useRef({ context, onTurn, onError });
  useEffect(() => {
    cb.current = { context, onTurn, onError };
  });

  const stopListening = useCallback(() => {
    cancelAnimationFrame(frame.current);
    if (rec.current && rec.current.state !== 'inactive') rec.current.stop();
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    void ctx.current?.close().catch(() => {});
    ctx.current = null;
    setLevel(0);
  }, []);

  const speak = useCallback((reply: VoiceReply, then: () => void) => {
    const text = reply.spoken?.mode === 'browser' ? (reply.spoken.text ?? reply.spoken_text) : null;
    if (!text || typeof speechSynthesis === 'undefined') return then();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = reply.spoken?.lang ?? 'en-US';
    u.onend = then;
    u.onerror = then;
    speechSynthesis.cancel();
    setState('speaking');
    setSaid(text);
    speechSynthesis.speak(u);
  }, []);

  const listenRef = useRef<() => Promise<void>>(async () => {});

  const finish = useCallback(
    async (blob: Blob | null, filename: string) => {
      if (!live.current) return;
      if (!blob || blob.size < 1500) {
        // Nothing was said: leave voice mode rather than keep the microphone open.
        live.current = false;
        setState('off');
        return;
      }
      const mine = ++turn.current;
      setState('thinking');
      abort.current = new AbortController();
      try {
        const { messages, confirmIds } = cb.current.context();
        const reply = await voiceCommand({ file: blob, filename, messages, confirmIds, signal: abort.current.signal });
        if (mine !== turn.current || !live.current) return;
        setHeard(reply.transcript);
        cb.current.onTurn(reply);
        const waiting = (reply.plan?.action_ids?.length ?? 0) > 0 || (reply.held?.length ?? 0) > 0;
        speak(reply, () => {
          if (!live.current || mine !== turn.current) return;
          // Keep the conversation going while something waits on a yes; otherwise rest until tapped.
          if (waiting) void listenRef.current();
          else {
            live.current = false;
            setState('off');
          }
        });
        if (reply.spoken?.mode !== 'browser') {
          if (waiting) void listenRef.current();
          else {
            live.current = false;
            setState('off');
          }
        }
      } catch (e) {
        if (mine !== turn.current) return;
        live.current = false;
        setState('off');
        if ((e as Error).name !== 'AbortError') cb.current.onError((e as Error).message);
      }
    },
    [speak],
  );

  const listen = useCallback(async () => {
    if (!canRecord()) {
      cb.current.onError(
        window.isSecureContext
          ? 'This browser can’t record. Try Chrome, Edge or Safari.'
          : 'Voice needs a secure (https) address. Open the app at its https address.',
      );
      live.current = false;
      setState('off');
      return;
    }
    let s: MediaStream;
    try {
      s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch {
      cb.current.onError('The microphone wasn’t allowed. Allow it in the browser’s site settings and try again.');
      live.current = false;
      setState('off');
      return;
    }
    if (!live.current) return s.getTracks().forEach((t) => t.stop());
    stream.current = s;
    const mimeType = TYPES.find((t) => MediaRecorder.isTypeSupported(t));
    const r = new MediaRecorder(s, mimeType ? { mimeType } : undefined);
    const chunks: Blob[] = [];
    r.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    r.onstop = () => {
      const type = (r.mimeType || mimeType || 'audio/webm').split(';')[0];
      void finish(chunks.length ? new Blob(chunks, { type }) : null, `voice${EXT[type] ?? '.webm'}`);
    };
    rec.current = r;
    // A level meter doubles as the "has the person stopped talking" check.
    const ac = new AudioContext();
    ctx.current = ac;
    const analyser = ac.createAnalyser();
    analyser.fftSize = 512;
    ac.createMediaStreamSource(s).connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    const began = Date.now();
    let spoke = false;
    let quietSince = Date.now();
    let shown = 0;
    setHeard('');
    setState('listening');
    r.start(250);
    const tick = () => {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += ((v - 128) / 128) ** 2;
      const rms = Math.sqrt(sum / data.length);
      const now = Date.now();
      if (now - shown > 66) {
        shown = now;
        setLevel(Math.min(1, rms * 6));
      }
      if (rms > SPEECH_LEVEL) {
        spoke = true;
        quietSince = now;
      }
      const done = (spoke && now - quietSince > PAUSE_MS) || (!spoke && now - began > WAIT_MS) || now - began > MAX_MS;
      if (done) {
        if (!spoke) chunks.length = 0; // silence is not sent
        stopListening();
        return;
      }
      frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
  }, [finish, stopListening]);

  useEffect(() => {
    listenRef.current = listen;
  }, [listen]);

  /** Opens voice mode and starts listening. */
  const start = useCallback(() => {
    live.current = true;
    setSaid('');
    void listen();
  }, [listen]);

  /** Closes voice mode: stops the microphone, the talking and any answer on its way. */
  const stop = useCallback(() => {
    live.current = false;
    turn.current++;
    abort.current?.abort();
    if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel();
    stopListening();
    setState('off');
  }, [stopListening]);

  /** Tapping the orb: while it talks, cut in and listen; while it listens, send what was said now. */
  const tap = useCallback(() => {
    if (state === 'speaking') {
      turn.current++;
      if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel();
      void listen();
    } else if (state === 'listening') stopListening();
  }, [state, listen, stopListening]);

  useEffect(() => stop, [stop]);

  return { state, level, heard, said, start, stop, tap };
}
