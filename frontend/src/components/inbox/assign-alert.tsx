'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { PersonSelect } from '@/components/data/pick';
import { Button } from '@/components/ui/button';
import { authFetch } from '@/lib/api';

/** Give an alert to someone else on the team. They see it in their own list and can close it. */
export function AssignAlert({ alertId }: { alertId: string }) {
  const client = useQueryClient();
  const [email, setEmail] = useState('');
  const give = useMutation({
    mutationFn: async () => {
      const res = await authFetch(`/api/v1/alerts/${encodeURIComponent(alertId)}/assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        const detail = await res
          .json()
          .then((j) => j?.detail)
          .catch(() => null);
        throw new Error(typeof detail === 'string' ? detail : `That didn’t work (${res.status}).`);
      }
    },
    onSuccess: () => {
      toast.success('Given to them');
      setEmail('');
      void client.invalidateQueries({ queryKey: ['inbox'] });
      void client.invalidateQueries({ queryKey: ['alerts'] });
    },
    onError: (e) => toast.error('It wasn’t given', { description: e.message }),
  });
  return (
    <div className="grid gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">Give it to someone</span>
      <div className="flex flex-wrap items-center gap-2">
        <PersonSelect value={email} onChange={setEmail} none="Choose a person" className="w-auto min-w-44" />
        <Button size="sm" variant="outline" disabled={!email || give.isPending} onClick={() => give.mutate()}>
          {give.isPending && <Loader2 className="animate-spin" />}
          Give it
        </Button>
        {!email && <span className="text-xs text-muted-foreground">Choose a person first</span>}
      </div>
    </div>
  );
}
