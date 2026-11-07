'use client';

// The floating assistant. It answers through tools on the server; anything
// that would change data comes back as a card the user confirms or cancels.
// The shortcut buttons run lookups directly, so they work with the AI off.

import { useEffect, useRef, useState } from 'react';
import { askAssistant, assistantLookup, decideAssistantAction, fetchAssistantConnectors } from '@/lib/opsApi';
import { useAuth } from '@/context/AuthContext';
import styles from './assistant.module.css';

const SHORTCUTS = [
  { label: 'What is late?', tool: 'schedule_status' },
  { label: 'Vendor risks', tool: 'vendor_status' },
  { label: 'Open RFQs', tool: 'rfq_status' },
  { label: 'My alerts', tool: 'my_alerts' },
];

const STORE = 'allied.assistant.v1';

function loadSaved() {
  try {
    const raw = sessionStorage.getItem(STORE);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export default function AssistantWidget() {
  const { isAuthenticated } = useAuth() || {};
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [showConnectors, setShowConnectors] = useState(false);
  const [connectors, setConnectors] = useState(null);
  const endRef = useRef(null);
  const loaded = useRef(false);

  useEffect(() => {
    if (open && !loaded.current) {
      loaded.current = true;
      const saved = loadSaved();
      // Restoring once on first open, not on every render.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (saved.length) setMessages(saved);
    }
  }, [open]);

  useEffect(() => {
    if (!loaded.current) return;
    try {
      sessionStorage.setItem(STORE, JSON.stringify(messages.slice(-30)));
    } catch {
      // storage can be unavailable; the chat still works
    }
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [messages]);

  useEffect(() => {
    if (!showConnectors) return undefined;
    let alive = true;
    fetchAssistantConnectors()
      .then((res) => alive && setConnectors(res.connectors))
      .catch((e) => alive && setConnectors([{ id: 'err', name: 'Could not load', ready: false, detail: e.message }]));
    return () => {
      alive = false;
    };
  }, [showConnectors]);

  if (!isAuthenticated) return null;

  const add = (m) => setMessages((list) => [...list, { id: `${Date.now()}-${Math.random()}`, ...m }]);

  async function send(text) {
    const question = (text ?? draft).trim();
    if (!question || busy) return;
    setDraft('');
    const history = [...messages, { role: 'user', content: question }]
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map(({ role, content }) => ({ role, content }));
    add({ role: 'user', content: question });
    setBusy(true);
    try {
      const res = await askAssistant(history.slice(-12));
      add({ role: 'assistant', content: res.reply, actions: res.actions, steps: res.steps });
    } catch (e) {
      add({ role: 'error', content: e.status === 503 ? `${e.message} The shortcut buttons below still work.` : e.message });
    } finally {
      setBusy(false);
    }
  }

  async function shortcut(s) {
    if (busy) return;
    add({ role: 'user', content: s.label });
    setBusy(true);
    try {
      const res = await assistantLookup(s.tool);
      add({ role: 'assistant', content: res.reply, lookup: true });
    } catch (e) {
      add({ role: 'error', content: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function decide(messageId, action, confirm) {
    const patch = (fields) =>
      setMessages((list) =>
        list.map((m) =>
          m.id !== messageId
            ? m
            : {
                ...m,
                actions: m.actions.map((a) => (a.id === action.id ? { ...a, ...fields } : a)),
              },
        ),
      );
    patch({ status: 'WORKING' });
    try {
      const res = await decideAssistantAction(action.id, confirm);
      patch({ status: res.status, result: res.result });
    } catch (e) {
      patch({ status: 'FAILED', result: { error: e.message } });
    }
  }

  return (
    <>
      <button
        type="button"
        className={styles.launcher}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="allied-assistant"
        aria-label={open ? 'Close the assistant' : 'Open the assistant'}
      >
        {open ? '×' : 'Ask'}
      </button>
      {open && (
        <section id="allied-assistant" className={styles.panel} aria-label="Assistant">
          <header className={styles.head}>
            <div>
              <strong>Assistant</strong>
              <span className={styles.sub}>Looks things up for you. Changes need your OK.</span>
            </div>
            <div className={styles.headActions}>
              <button type="button" className={styles.link} onClick={() => setShowConnectors((v) => !v)}>
                {showConnectors ? 'Chat' : 'Connections'}
              </button>
              {messages.length > 0 && (
                <button type="button" className={styles.link} onClick={() => setMessages([])}>
                  Clear
                </button>
              )}
            </div>
          </header>

          {showConnectors ? (
            <div className={styles.body}>
              {!connectors ? (
                <p className={styles.muted}>Checking…</p>
              ) : (
                <ul className={styles.connectors}>
                  {connectors.map((c) => (
                    <li key={c.id}>
                      <span className={c.ready ? styles.dotOn : styles.dotOff} aria-hidden="true" />
                      <div>
                        <strong>{c.name}</strong>
                        {c.tools != null && <span className={styles.muted}> · {c.tools} tools</span>}
                        <p className={styles.muted}>{c.detail}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <p className={styles.muted}>Other services connect through MCP servers listed in ASSISTANT_MCP_SERVERS.</p>
            </div>
          ) : (
            <div className={styles.body} aria-live="polite">
              {messages.length === 0 && (
                <p className={styles.muted}>
                  Ask about projects, vendor dates, RFQs, shipments or orders. For example: “Which vendors could make the Vulcan job late?”
                  or “Mark RFQ-2026-0004 as waiting on Zibo for pricing.”
                </p>
              )}
              {messages.map((m) => (
                <div key={m.id} className={styles[m.role] || styles.assistant}>
                  <p className={styles.text}>{m.content}</p>
                  {m.steps?.length > 0 && (
                    <p className={styles.steps}>Looked at: {[...new Set(m.steps.map((s) => s.tool.replace(/_/g, ' ')))].join(', ')}</p>
                  )}
                  {m.actions?.map((a) => (
                    <div key={a.id} className={styles.action}>
                      <p className={styles.actionTitle}>Waiting for your OK</p>
                      <pre className={styles.summary}>{a.summary}</pre>
                      {(!a.status || a.status === 'PENDING') && (
                        <div className={styles.actionButtons}>
                          <button type="button" className={styles.confirm} onClick={() => decide(m.id, a, true)}>
                            Do it
                          </button>
                          <button type="button" className={styles.cancel} onClick={() => decide(m.id, a, false)}>
                            Cancel
                          </button>
                        </div>
                      )}
                      {a.status === 'WORKING' && <p className={styles.muted}>Working…</p>}
                      {a.status === 'DONE' && <p className={styles.ok}>Done. {a.result?.done}</p>}
                      {a.status === 'CANCELLED' && <p className={styles.muted}>Cancelled. Nothing was changed.</p>}
                      {a.status === 'FAILED' && <p className={styles.bad}>Not done: {a.result?.error}</p>}
                    </div>
                  ))}
                </div>
              ))}
              {busy && <p className={styles.muted}>Thinking…</p>}
              <div ref={endRef} />
            </div>
          )}

          {!showConnectors && (
            <footer className={styles.foot}>
              <div className={styles.shortcuts}>
                {SHORTCUTS.map((s) => (
                  <button key={s.tool} type="button" className={styles.chip} disabled={busy} onClick={() => shortcut(s)}>
                    {s.label}
                  </button>
                ))}
              </div>
              <form
                className={styles.form}
                onSubmit={(e) => {
                  e.preventDefault();
                  send();
                }}
              >
                <textarea
                  className={styles.input}
                  rows={2}
                  value={draft}
                  placeholder="Ask a question…"
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                />
                <button type="submit" className={styles.send} disabled={busy || !draft.trim()}>
                  Send
                </button>
              </form>
            </footer>
          )}
        </section>
      )}
    </>
  );
}
