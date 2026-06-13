/**
 * Direct Neon Postgres client for the site's read-only stats. Lazy: the
 * factory is built on first use, so next build succeeds when DATABASE_URL is
 * unset and every caller fails soft to static fallbacks. The server-side
 * DATABASE_URL (never NEXT_PUBLIC_) is only needed when a query fires.
 */

import { neon, type NeonQueryFunction } from "@neondatabase/serverless";

let cached: NeonQueryFunction<false, false> | null = null;

export function sql(
  strings: TemplateStringsArray,
  ...params: unknown[]
): ReturnType<NeonQueryFunction<false, false>> {
  if (cached === null) {
    const url = process.env.DATABASE_URL;
    if (!url) {
      throw new Error("DATABASE_URL is not set; live stats are unavailable.");
    }
    cached = neon(url);
  }
  return cached(strings, ...params);
}
