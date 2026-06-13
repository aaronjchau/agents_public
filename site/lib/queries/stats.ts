/**
 * Lifetime aggregate counts from the production audit tables.
 *
 * Deliberately coarse: total rows only, no timestamps, no content, the
 * one place the public site touches the live database. Pages that use
 * these render with ISR (revalidate), so the query runs at most once
 * per revalidation window, never per visitor.
 *
 * Fails soft: any error (including a missing DATABASE_URL on local
 * checkouts of the mirror) returns null and callers fall back to static
 * copy or hide their live sections.
 */

import { sql } from "@/lib/db";

export interface LifetimeStats {
  /** Rows in triager_runs, one per email classified since inception. */
  emailsClassified: number;
  /** Rows in news_brief_runs, news briefs published to Notion. */
  newsBriefs: number;
  /** Rows in morning_brief_runs, morning briefs published to Notion. */
  morningBriefs: number;
}

interface StatsRawRow {
  emails_classified: string | number;
  news_briefs: string | number;
  morning_briefs: string | number;
}

export async function fetchLifetimeStats(): Promise<LifetimeStats | null> {
  try {
    const rows = (await sql`
      SELECT
        (SELECT count(*) FROM triager_runs)       AS emails_classified,
        (SELECT count(*) FROM news_brief_runs)    AS news_briefs,
        (SELECT count(*) FROM morning_brief_runs) AS morning_briefs
    `) as unknown as StatsRawRow[];
    const row = rows[0];
    if (!row) return null;
    return {
      emailsClassified: Number(row.emails_classified),
      newsBriefs: Number(row.news_briefs),
      morningBriefs: Number(row.morning_briefs),
    };
  } catch (err) {
    console.error("fetchLifetimeStats failed; rendering static fallbacks", err);
    return null;
  }
}
