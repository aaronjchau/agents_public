import { CountUp } from "@/components/count-up";
import { SectionHead } from "@/components/section-head";
import { type LifetimeStats } from "@/lib/queries/stats";
import { STATIC_STATS } from "@/lib/site";

/** Live lifetime totals from the audit ledger; falls back to static snapshot numbers (no LIVE badge) when the DB is unreachable. */
export function LiveLedger({ stats }: { stats: LifetimeStats | null }) {
  if (!stats) {
    return (
      <section className="sec" style={{ paddingTop: 40 }}>
        <SectionHead
          index="00 / LIVE"
          title="Live ledger"
          note="lifetime totals · refreshed hourly"
        />
        <div className="counters anim-scope">
          <div className="counter reveal">
            <span className="num">
              <CountUp value={STATIC_STATS.emailsClassified} />
            </span>
            <span className="cap">emails classified</span>
          </div>
          <div className="counter reveal">
            <span className="num">
              <CountUp value={STATIC_STATS.newsBriefs} />
            </span>
            <span className="cap">news briefs generated</span>
          </div>
          <div className="counter reveal">
            <span className="num">
              <CountUp value={STATIC_STATS.morningBriefs} />
            </span>
            <span className="cap">morning briefs generated</span>
          </div>
        </div>
      </section>
    );
  }
  return (
    <section className="sec" style={{ paddingTop: 40 }}>
      <SectionHead
        index="00 / LIVE"
        title="Live ledger"
        note="lifetime totals · refreshed hourly"
      />
      <div className="counters anim-scope">
        <div className="counter reveal live-counter">
          <span className="live-tag">
            <i className="live-led" />
            LIVE
          </span>
          <span className="num">
            <CountUp value={stats.emailsClassified} />
          </span>
          <span className="cap">emails classified</span>
        </div>
        <div className="counter reveal live-counter">
          <span className="live-tag">
            <i className="live-led" style={{ "--ld": "0.45s" }} />
            LIVE
          </span>
          <span className="num">
            <CountUp value={stats.newsBriefs} />
          </span>
          <span className="cap">news briefs generated</span>
        </div>
        <div className="counter reveal live-counter">
          <span className="live-tag">
            <i className="live-led" style={{ "--ld": "0.9s" }} />
            LIVE
          </span>
          <span className="num">
            <CountUp value={stats.morningBriefs} />
          </span>
          <span className="cap">morning briefs generated</span>
        </div>
      </div>
    </section>
  );
}
