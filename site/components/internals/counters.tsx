import { CountUp } from "@/components/count-up";
import { type LifetimeStats } from "@/lib/queries/stats";
import { COST_PER_EMAIL_USD, STATIC_STATS, TEST_COUNT } from "@/lib/site";

/** Stat band; falls back to static snapshot numbers when the DB is unreachable. */
export function Counters({ stats }: { stats: LifetimeStats | null }) {
  if (!stats) {
    return (
      <div className="counters">
        <div className="counter reveal">
          <span className="num">
            <CountUp value={STATIC_STATS.emailsClassified} />
          </span>
          <span className="cap">emails classified</span>
        </div>
        <div className="counter reveal">
          <span className="num">
            <CountUp value={STATIC_STATS.newsBriefs + STATIC_STATS.morningBriefs} />
          </span>
          <span className="cap">briefs published</span>
        </div>
        <div className="counter reveal">
          <span className="num">
            $<CountUp value={COST_PER_EMAIL_USD} decimals={3} />
          </span>
          <span className="cap">median per email, prompt-cached</span>
        </div>
        <div className="counter reveal">
          <span className="num">
            <CountUp value={TEST_COUNT} />
          </span>
          <span className="cap">tests · zero secrets</span>
        </div>
      </div>
    );
  }
  return (
    <div className="counters">
      <div className="counter reveal">
        <span className="num">
          <CountUp value={stats.emailsClassified} />
        </span>
        <span className="cap">emails classified · live</span>
      </div>
      <div className="counter reveal">
        <span className="num">
          <CountUp value={stats.newsBriefs + stats.morningBriefs} />
        </span>
        <span className="cap">briefs published · live</span>
      </div>
      <div className="counter reveal">
        <span className="num">
          $<CountUp value={COST_PER_EMAIL_USD} decimals={3} />
        </span>
        <span className="cap">per email, prompt-cached</span>
      </div>
      <div className="counter reveal">
        <span className="num">
          <CountUp value={TEST_COUNT} />
        </span>
        <span className="cap">tests · zero secrets</span>
      </div>
    </div>
  );
}
