import type { Metadata } from "next";
import Link from "next/link";

import { ROUTES } from "@/lib/site";

export const metadata: Metadata = { title: "404" };

const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight"];
const ROUTE_COUNT_WORD = NUMBER_WORDS[ROUTES.length] ?? String(ROUTES.length);

export default function NotFound() {
  return (
    <main className="page-anim">
      <div className="wrap">
        {/* no .reveal here: a 404 shell may never hydrate, and hidden states would stick */}
        <section className="hero solo">
          <div>
            <h1>
              4<em>0</em>4
            </h1>
            <p
              className="mono"
              style={{
                marginTop: 10,
                fontSize: 11,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: "var(--faint)",
              }}
            >
              no route registered here
            </p>
            <p>
              Nothing is mounted at this path. The {ROUTE_COUNT_WORD} registered routes hang off
              the index.
            </p>
            <div style={{ marginTop: 26 }}>
              <Link href="/" className="btn">
                fleet
              </Link>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
