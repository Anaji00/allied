import type { QueryClient, QueryKey } from '@tanstack/react-query';

type Snapshot = [QueryKey, unknown][];

/**
 * Shows a save the moment it's made: `apply` rewrites every cached query under
 * `queryKey` straight away, the server's refusal puts the old data back, and
 * either way the queries refresh from the server afterwards.
 *
 * Spread the result into useMutation and call `rollback` from your own onError
 * (so the error toast still shows).
 */
export function optimistic<V>(client: QueryClient, queryKey: QueryKey, apply: (old: never, vars: V) => unknown) {
  return {
    onMutate: async (vars: V): Promise<{ snapshot: Snapshot }> => {
      // A refetch landing mid-save would overwrite the optimistic value.
      await client.cancelQueries({ queryKey });
      const snapshot = client.getQueriesData({ queryKey });
      client.setQueriesData({ queryKey }, (old) => (old === undefined ? old : apply(old as never, vars)));
      return { snapshot };
    },
    rollback: (context: { snapshot: Snapshot } | undefined) => context?.snapshot.forEach(([key, data]) => client.setQueryData(key, data)),
    onSettled: () => client.invalidateQueries({ queryKey }),
  };
}
