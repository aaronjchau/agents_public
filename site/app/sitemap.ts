import type { MetadataRoute } from "next";

import { ROUTES, SITE_URL } from "@/lib/site";

export default function sitemap(): MetadataRoute.Sitemap {
  return ROUTES.map((r) => ({
    url: new URL(r.href, SITE_URL).href,
  }));
}
