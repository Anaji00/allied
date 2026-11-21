'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { PersonSelect } from '@/components/data/pick';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { authFetch } from '@/lib/api';

/**
 * Pick who owns an item. It is staged on the monday changes list, never sent from here, so nothing on monday
 * moves until someone sends the list. Only boards with a People column can take it.
 */
export function OwnerPicker({ boardId, itemId }: { boardId: string; itemId: string }) {
  const { hasRole } = useAuth();
  const client = useQueryClient();
  const [email, setEmail] = useState('');
  const stage = useMutation({
    mutationFn: async () => {
      const res = await authFetch(`/api/v1/boards/${encodeURIComponent(boardId)}/items/${encodeURIComponent(itemId)}/owner`, {
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
      toast.success('Owner saved, waiting to go to monday', {
        description: 'It is on the monday changes list. Nothing is sent until someone sends it.',
      });
      setEmail('');
      void client.invalidateQueries({ queryKey: ['monday-push'] });
    },
    onError: (e) => toast.error('The owner wasn’t saved', { description: e.message }),
  });
  if (!hasRole('OPERATOR')) return null;
  return (
    <div className="grid gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">Set the owner</span>
      <div className="flex flex-wrap items-center gap-2">
        <PersonSelect area="engineering" value={email} onChange={setEmail} none="Choose a person" className="w-auto min-w-48" />
        <Button size="sm" variant="outline" disabled={!email || stage.isPending} onClick={() => stage.mutate()}>
          {stage.isPending && <Loader2 className="animate-spin" />}
          Save for monday
        </Button>
        {!email && <span className="text-xs text-muted-foreground">Choose a person first</span>}
      </div>
    </div>
  );
}
