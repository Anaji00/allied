'use client';

import { useEffect, useRef, useState } from 'react';

// Live speech to text in the browser (Chrome and Edge). Nothing is uploaded by
// this app; no server model is needed. Recordings use the server instead.
const LOCALES = { en: 'en-US', vi: 'vi-VN', zh: 'zh-CN' };

export function useDictation(onText) {
  const recognition = useRef(null);
  const latest = useRef(onText);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    latest.current = onText;
  });

  useEffect(() => () => recognition.current?.abort(), []);

  function start(language = 'en') {
    const SR = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition);
    if (!SR) {
      setError('Dictation needs Chrome or Edge.');
      return;
    }
    const rec = new SR();
    rec.lang = LOCALES[language] || 'en-US';
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        if (event.results[i].isFinal) latest.current(event.results[i][0].transcript.trim());
      }
    };
    rec.onerror = (event) => {
      setError(event.error === 'not-allowed' ? 'Allow microphone access to dictate.' : `Dictation stopped (${event.error}).`);
    };
    rec.onend = () => setListening(false);
    recognition.current = rec;
    setError('');
    setListening(true);
    rec.start();
  }

  function stop() {
    recognition.current?.stop();
  }

  return { listening, error, start, stop };
}
