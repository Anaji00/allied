import { cn } from '@/lib/utils';

/** The Allied diamond. Red is the logo's colour; nothing else in the shell uses it. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true" className={cn('size-6 shrink-0', className)}>
      <polygon points="20,0 40,20 20,40 0,20" className="fill-brand" />
      <polygon points="20,8 32,20 20,32 8,20" className="fill-foreground" />
      <path d="M20,12 L26,20 L20,28 L14,20 Z" className="fill-brand" />
    </svg>
  );
}
