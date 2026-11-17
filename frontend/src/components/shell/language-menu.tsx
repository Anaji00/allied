'use client';

import { useState } from 'react';
import { Languages } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DropdownMenuGroup, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem } from '@/components/ui/dropdown-menu';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useLanguage, type LangCode } from '@/lib/language';

/** The language choice inside the account menu. Hidden until the server supports languages. */
export function LanguageMenuGroup() {
  const { available, language, options, set } = useLanguage();
  if (!available) return null;
  return (
    <DropdownMenuGroup>
      <DropdownMenuLabel className="flex items-center gap-1.5">
        <Languages className="size-3.5" /> Language
      </DropdownMenuLabel>
      <DropdownMenuRadioGroup value={language} onValueChange={(v) => set(v as LangCode)}>
        {options.map((o) => (
          <DropdownMenuRadioItem key={o.code} value={o.code}>
            {o.label}
          </DropdownMenuRadioItem>
        ))}
      </DropdownMenuRadioGroup>
    </DropdownMenuGroup>
  );
}

const ASKED = 'allied.language.asked';

/** Asked once, on first use: which language comments and forms should be shown in. */
export function LanguagePrompt() {
  const { available, options, set } = useLanguage();
  // Private windows refuse storage: then the question is skipped rather than asked every time.
  const [asked, setAsked] = useState(() => {
    try {
      return typeof window === 'undefined' || Boolean(localStorage.getItem(ASKED));
    } catch {
      return true;
    }
  });
  const open = available && !asked;
  const close = () => {
    try {
      localStorage.setItem(ASKED, '1');
    } catch {
      // nothing to remember it with
    }
    setAsked(true);
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && close()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Which language do you read?</DialogTitle>
          <DialogDescription>
            Comments, forms, and navigation will be shown in your language. You can change this any time from the menu at the top right.
            <span className="mt-1.5 block text-xs">
              Tiếng Việt: Chọn ngôn ngữ của bạn bên dưới. · 中文：请在下方选择您的语言。
            </span>
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-2">
          {options.map((o) => (
            <Button
              key={o.code}
              variant="outline"
              size="lg"
              onClick={() => {
                set(o.code);
                close();
              }}
            >
              {o.label}
            </Button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
