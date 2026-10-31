import createClient from 'openapi-fetch';
import { authFetch } from '@/lib/api';
import type { paths } from './schema';

/**
 * Typed client for the FastAPI backend.
 *
 * Paths, parameters and response bodies come from `schema.d.ts`, generated from
 * the API's OpenAPI schema (`npm run api:types`), so a renamed field or route
 * fails the type check instead of failing in front of staff.
 *
 * Requests go through `authFetch` from the older client, so both share one
 * token, one refresh-on-401 and one sign-out path while pages move over.
 */
export const api = createClient<paths>({
  baseUrl: '',
  fetch: async (request: Request) => {
    const body = request.body ? await request.arrayBuffer() : undefined;
    return authFetch(request.url, {
      method: request.method,
      headers: request.headers,
      body,
      cache: 'no-store',
      signal: request.signal,
    });
  },
});

/** The one error every API call raises: the status plus the server's own words. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** FastAPI sends `detail` as a sentence, or as a list of field errors on a 422. */
function describe(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        const field = Array.isArray(d?.loc) ? d.loc.filter((p: unknown) => p !== 'body').join('.') : '';
        return field ? `${field}: ${d?.msg ?? 'invalid'}` : d?.msg;
      })
      .filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  if (status === 401) return 'Your sign-in has expired. Sign in again.';
  if (status === 403) return 'Your role does not allow this.';
  return `Request failed (${status}).`;
}

/**
 * Unwraps an openapi-fetch result: the data on success, an ApiError otherwise.
 *
 *   const health = await ok(api.GET('/api/v1/health'));
 */
export async function ok<T>(
  pending: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await pending;
  if (!response.ok || error !== undefined) throw new ApiError(response.status, describe(error, response.status));
  return data as T;
}
