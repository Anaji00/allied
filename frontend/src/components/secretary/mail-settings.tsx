'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Field, PanelHeading } from '@/components/data/filters';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { authFetch } from '@/lib/api';

import { useAiHealth, useMailSettings, type MailSettings as Settings } from './use-mail-ai';

const PATH = '/api/v1/me/mail-settings';

const list = (text: string) =>
  text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);

/** Each person decides whether the AI helper may write replies for them, and what it must never read. Off until switched on. */
export function MailSettings() {
  const q = useMailSettings();
  if (q.isPending) return null;
  if (!q.data)
    return (
      <section id="reply-helper" className="grid gap-1 rounded-lg border bg-card p-4" aria-label="Reply helper">
        <PanelHeading>Reply helper</PanelHeading>
        <p className="text-[13px] text-muted-foreground">
          Not ready yet. The server needs an update before you can choose how replies are written for you.
        </p>
      </section>
    );
  return <SettingsForm initial={q.data} />;
}

function SettingsForm({ initial }: { initial: Settings }) {
  const client = useQueryClient();
  const ai = useAiHealth();
  const aiOff = !ai.data?.ready;
  const [form, setForm] = useState<Settings>(initial);
  const [senders, setSenders] = useState(initial.exclude_senders.join(', '));
  const [wordsText, setWordsText] = useState(initial.exclude_words.join(', '));

  const save = useMutation({
    mutationFn: async () => {
      const body: Settings = { ...form, exclude_senders: list(senders), exclude_words: list(wordsText) };
      const res = await authFetch(PATH, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => null);
        throw new Error(typeof j?.detail === 'string' ? j.detail : `That didn’t save (${res.status}).`);
      }
    },
    onSuccess: () => {
      toast.success('Saved');
      void client.invalidateQueries({ queryKey: ['mail-settings'] });
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  });

  return (
    <section id="reply-helper" className="grid gap-3 rounded-lg border bg-card p-4" aria-label="Reply helper">
      <PanelHeading>Reply helper</PanelHeading>
      <label className="flex items-start gap-2 text-[13px]">
        <input
          type="checkbox"
          className="mt-0.5 size-4"
          checked={form.ai_opt_in}
          disabled={aiOff && !form.ai_opt_in}
          onChange={(e) => setForm({ ...form, ai_opt_in: e.target.checked, auto_draft: e.target.checked && form.auto_draft })}
        />
        <span>
          <span className="font-medium">Let the AI helper write replies for me</span>
          <span className="block text-xs text-muted-foreground">
            When on, the text of the mail you ask about is sent to our AI provider to write the draft. It is off until you switch it on. A
            person still approves every reply.
          </span>
          {aiOff && <span className="block text-xs text-warn">Not available right now: {ai.data?.reason ?? 'checking…'}</span>}
        </span>
      </label>
      <label className={`flex items-start gap-2 text-[13px] ${form.ai_opt_in ? '' : 'opacity-50'}`}>
        <input
          type="checkbox"
          className="mt-0.5 size-4"
          disabled={!form.ai_opt_in || aiOff}
          checked={form.auto_draft}
          onChange={(e) => setForm({ ...form, auto_draft: e.target.checked })}
        />
        <span>
          <span className="font-medium">Have a draft ready when mail needs a reply</span>
          <span className="block text-xs text-muted-foreground">Drafts wait in Approvals. Nothing is sent for you.</span>
        </span>
      </label>
      <Field label="How I write" hint="A line or two: greeting, sign-off, language.">
        <Textarea
          rows={2}
          maxLength={500}
          value={form.style_note}
          onChange={(e) => setForm({ ...form, style_note: e.target.value })}
          placeholder="Short and polite. Start with Hi, end with Thanks."
        />
      </Field>
      <Field label="Never send mail from these people to the AI" hint="Email addresses or domains, separated by commas.">
        <Input value={senders} onChange={(e) => setSenders(e.target.value)} placeholder="hr@example.com, lawyer.com" />
      </Field>
      <Field label="Never send mail with these words to the AI" hint="Separated by commas.">
        <Input value={wordsText} onChange={(e) => setWordsText(e.target.value)} placeholder="confidential, salary" />
      </Field>
      <div>
        <Button size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending && <Loader2 className="animate-spin" />} Save
        </Button>
      </div>
    </section>
  );
}
