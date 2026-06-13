import type { Metadata } from "next";

import { FanIn } from "@/components/briefs/fan-in";
import { Guarantees } from "@/components/briefs/guarantees";
import { NotionDocs } from "@/components/briefs/notion-docs";
import { SieveFig } from "@/components/briefs/sieve-fig";
import { Cta } from "@/components/cta";
import { SectionHead } from "@/components/section-head";

export const metadata: Metadata = {
  title: "briefs",
  description:
    "Two cron agents write the morning reading: a news digest at 9:15 and a task-and-calendar brief at 10:30, each published to Notion once per day.",
};

export default function BriefsPage() {
  return (
    <main className="page-anim">
      <div className="wrap">
        <section className="hero solo">
          <div>
            <h1 className="reveal-now">
              Two pages, <em>every morning</em>.
            </h1>
            <p className="reveal-now">
              The News Brief turns yesterday&apos;s newsletters into a digest at 9:15. The Morning
              Brief gathers tasks, calendar, focus stats, and flagged email into one page at 10:30.
              Both publish to Notion once per day.
            </p>
            <div className="statline reveal-now">
              <span>
                <b>2</b> pages / day
              </span>
              <span>
                <b>1</b> LLM call each
              </span>
              <span>
                <b>0</b> raw URLs shown to the model
              </span>
              <span>
                <b>0</b> duplicate days
              </span>
            </div>
          </div>
        </section>

        <section className="sec" style={{ paddingTop: 40 }}>
          <SectionHead
            index="01"
            title="The sieve"
            note="news brief · cron 09:15 ET · news-labeled email from the last 24h"
          />
          <SieveFig />
        </section>

        <section className="sec">
          <SectionHead
            index="02"
            title="The fan-in"
            note="morning brief · cron 10:30 ET · five sources, one page"
          />
          <FanIn />
        </section>

        <section className="sec">
          <SectionHead
            index="03"
            title="Notion output"
            note="strict formats · one quote-line per story"
          />
          <NotionDocs />
        </section>

        <section className="sec">
          <SectionHead index="04" title="Guarantees" note="publishing discipline" />
          <Guarantees />
        </section>

        <Cta
          title={
            <>
              Two pages, <em>published daily</em>.
            </>
          }
          blurb="Plain Python and one model call each."
          nextHref="/internals"
          nextLabel="internals →"
        />
      </div>
    </main>
  );
}
