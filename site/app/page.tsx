import { Cta } from "@/components/cta";
import { DayRails } from "@/components/fleet/day-rails";
import { HardRules } from "@/components/fleet/hard-rules";
import { LiveLedger } from "@/components/fleet/live-ledger";
import { Orbit } from "@/components/fleet/orbit";
import { SystemFig } from "@/components/fleet/system-fig";
import { SectionHead } from "@/components/section-head";
import { fetchLifetimeStats } from "@/lib/queries/stats";
import { TEST_COUNT } from "@/lib/site";

export const revalidate = 3600;

export default async function FleetPage() {
  const stats = await fetchLifetimeStats();
  return (
    <main className="page-anim">
      <div className="wrap">
        <section className="hero">
          <div>
            <h1 className="reveal-now">
              Five agents in orbit around <em>one database</em>.
            </h1>
            <p className="reveal-now">
              Two agents run the moment an email arrives. Three run on daily schedules. Every run
              writes one audit row to Postgres with tokens, cost, and timing.
            </p>
            <div className="statline reveal-now">
              <span>
                <b>12</b> labels
              </span>
              <span>
                <b>3</b> daily crons
              </span>
              <span>
                <b>{TEST_COUNT}</b> tests
              </span>
              <span>
                <b>1</b> LangGraph
              </span>
            </div>
          </div>

          <Orbit />
        </section>

        <LiveLedger stats={stats} />

        <section className="sec">
          <SectionHead
            index="01"
            title="System design"
            note="label ≈ 7 s · job-app pipeline ≈ 29 s"
          />
          <SystemFig />
        </section>

        <section className="sec">
          <SectionHead index="02" title="The daily schedule" />
          <DayRails />
        </section>

        <section className="sec">
          <SectionHead index="03" title="Hard rules" />
          <HardRules />
        </section>

        <Cta
          title={
            <>
              The engineering is <em>public</em>.
            </>
          }
          blurb="Prompts and personal data are removed. The pipeline logic, tests, and infra are intact."
          nextHref="/email"
          nextLabel="email →"
        />
      </div>
    </main>
  );
}
