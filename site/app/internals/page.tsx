import type { Metadata } from "next";

import { Cta } from "@/components/cta";
import { Counters } from "@/components/internals/counters";
import { RunArtifacts } from "@/components/internals/run-artifacts";
import { StackPanel } from "@/components/internals/stack-panel";
import { TimingFig } from "@/components/internals/timing-fig";
import { SectionHead } from "@/components/section-head";
import { fetchLifetimeStats } from "@/lib/queries/stats";
import { TEST_COUNT } from "@/lib/site";

export const metadata: Metadata = {
  title: "internals",
  description:
    "One triager run, stage by stage: per-stage timings, the audit row it leaves in Postgres, the stack underneath, and lifetime totals.",
};

export const revalidate = 3600;

export default async function InternalsPage() {
  const stats = await fetchLifetimeStats();
  return (
    <main className="page-anim">
      <div className="wrap">
        <section className="hero solo">
          <div>
            <h1 className="reveal-now">
              Anatomy of <em>one run</em>.
            </h1>
            <p className="reveal-now">
              An email lands. About seven seconds and one model call later it&apos;s a labeled
              message with an audit row. Job-app email runs the graph for another twenty seconds
              into Notion.
            </p>
          </div>
        </section>

        {/* tighter top pad than .sec for the first section */}
        <section style={{ paddingTop: "8px" }}>
          <SectionHead
            index="01"
            title="One run, timed"
            note="medians from the audit ledger, stored as JSONB"
          />
          <TimingFig />
        </section>

        <section className="sec">
          <SectionHead
            index="02"
            title="Run artifacts"
            note="triager_runs · one row per run"
          />
          <RunArtifacts />
        </section>

        <section className="sec">
          <SectionHead index="03" title="Stack" note="boring infrastructure, on purpose" />
          <StackPanel />
        </section>

        <section className="sec">
          <SectionHead
            index="04"
            title="By the numbers"
            note="spend-sync reconciles these nightly"
          />
          <Counters stats={stats} />
        </section>

        <Cta
          title={
            <>
              <em>make test.</em> {TEST_COUNT} green.
            </>
          }
          blurb="The whole suite runs with zero credentials."
          nextHref="/"
          nextLabel="← fleet"
        />
      </div>
    </main>
  );
}
