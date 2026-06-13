/** Site-wide constants. */

/** The public source repository this site documents. */
export const REPO_URL = "https://github.com/aaronjchau/agents_public";

/** Canonical production URL (metadata base, sitemap, robots). */
export const SITE_URL = "https://agents.aaronjchau.com";

/** Passing tests in the synced backend suite (make test, zero secrets). */
export const TEST_COUNT = 602;

/** No-DB fallback snapshot of lifetime audit totals, shown when the DB is unreachable. */
export const STATIC_STATS = {
  emailsClassified: 1140,
  newsBriefs: 28,
  morningBriefs: 3,
};

/** Median cost_usd across non-errored triager_runs. */
export const COST_PER_EMAIL_USD = 0.01;

/** Registered routes; source of truth for the nav, sitemap, and 404 count. */
export const ROUTES = [
  { href: "/", label: "FLEET" },
  { href: "/email", label: "EMAIL" },
  { href: "/briefs", label: "BRIEFS" },
  { href: "/internals", label: "INTERNALS" },
];
