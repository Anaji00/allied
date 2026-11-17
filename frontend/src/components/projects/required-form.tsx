'use client';

import { useQuery } from '@tanstack/react-query';
import { Field } from '@/components/data/filters';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { authFetch } from '@/lib/api';

export type FormField = {
  key: string;
  label: string;
  type: 'text' | 'longtext' | 'choice' | 'number' | 'date' | 'yesno';
  required?: boolean;
  help?: string;
  options?: string[];
  min_chars?: number;
  must_be_yes?: boolean;
};
export type FormTemplate = {
  id: string;
  name: string;
  enabled: boolean;
  applies_to: string[];
  instructions: string;
  fields: FormField[];
};
export type Answers = Record<string, string | boolean>;
/** The same form's words in another language. Answers and option values stay English. */
export type FormTranslation = {
  lang: string;
  name?: string;
  instructions?: string;
  fields: Record<string, { label?: string; help?: string; options?: Record<string, string> }>;
  complete?: boolean;
};

/** The form in the reader's language; nothing is fetched for English. */
export function useTranslatedForm(templateId: string | undefined, lang: string) {
  return useQuery({
    queryKey: ['forms', 'translated', templateId, lang],
    queryFn: async (): Promise<FormTranslation | null> => {
      const res = await authFetch(`/api/v1/forms/templates/${encodeURIComponent(templateId!)}/translated?lang=${lang}`, {
        cache: 'no-store',
      });
      return res.ok ? ((await res.json()) as FormTranslation) : null;
    },
    enabled: Boolean(templateId) && lang !== 'en',
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** The form a milestone needs filled in before it is sent, if any. */
export function useMilestoneForm(milestoneName: string) {
  return useQuery({
    queryKey: ['forms', 'for-milestone', milestoneName],
    queryFn: async (): Promise<FormTemplate | null> => {
      const res = await authFetch(`/api/v1/forms/for-milestone?name=${encodeURIComponent(milestoneName)}`, { cache: 'no-store' });
      // An older server has no forms: send as before.
      if (!res.ok) return null;
      return (await res.json()) as FormTemplate | null;
    },
    retry: false,
  });
}

/** What is still missing, in plain words, so the button can wait. The server checks again when it is sent. */
export function missingAnswers(form: FormTemplate, answers: Answers): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of form.fields) {
    const v = answers[f.key];
    const text = typeof v === 'string' ? v.trim() : '';
    if (f.type === 'yesno') {
      if (f.must_be_yes && v !== true) out[f.key] = 'This needs a tick.';
    } else if (f.required && !text) out[f.key] = 'This is needed.';
    else if (f.min_chars && text && text.length < f.min_chars) out[f.key] = `Say a little more (at least ${f.min_chars} characters).`;
  }
  return out;
}

const SELECT =
  'h-8 rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50';

export function RequiredForm({
  form,
  answers,
  onChange,
  errors,
  translation,
}: {
  translation?: FormTranslation | null;
  form: FormTemplate;
  answers: Answers;
  onChange: (key: string, value: string | boolean) => void;
  errors: Record<string, string>;
}) {
  return (
    <div className="grid gap-3 rounded-md bg-muted/40 p-3">
      <div className="grid gap-0.5">
        <p className="text-sm font-medium">{translation?.name || form.name}</p>
        {(translation?.instructions || form.instructions) && (
          <p className="text-xs text-muted-foreground">{translation?.instructions || form.instructions}</p>
        )}
        {translation && <p className="text-xs text-muted-foreground">Answers are sent in English.</p>}
      </div>
      {form.fields.map((f) => {
        const tr = translation?.fields?.[f.key];
        const text = tr?.label || f.label;
        const help = tr?.help || f.help;
        const label = f.required || f.must_be_yes ? `${text} *` : text;
        const err = errors[f.key];
        const value = String(answers[f.key] ?? '');
        return (
          <div key={f.key} className="grid gap-1">
            {f.type === 'yesno' ? (
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1 size-4"
                  checked={answers[f.key] === true}
                  onChange={(e) => onChange(f.key, e.target.checked)}
                  aria-invalid={Boolean(err)}
                />
                <span>
                  {label}
                  {help && <span className="block text-xs text-muted-foreground">{help}</span>}
                </span>
              </label>
            ) : (
              <Field label={label} hint={help}>
                {f.type === 'longtext' ? (
                  <Textarea rows={3} value={value} onChange={(e) => onChange(f.key, e.target.value)} aria-invalid={Boolean(err)} />
                ) : f.type === 'choice' ? (
                  <select className={SELECT} value={value} onChange={(e) => onChange(f.key, e.target.value)} aria-invalid={Boolean(err)}>
                    <option value="">Choose…</option>
                    {(f.options ?? []).map((o) => (
                      <option key={o} value={o}>
                        {tr?.options?.[o] ?? o}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    type={f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text'}
                    value={value}
                    onChange={(e) => onChange(f.key, e.target.value)}
                    aria-invalid={Boolean(err)}
                  />
                )}
              </Field>
            )}
            {err && (
              <p role="alert" className="text-xs text-bad">
                {err}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
