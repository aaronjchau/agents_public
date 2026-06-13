/**
 * Per-stage timing bars for one triager to job-apps run. Values are per-stage
 * medians from the audit ledger (triager_runs.stage_timings_ms, n=805;
 * job_apps_runs.node_timings_ms, n=14). db_write/dispatch have no recorded
 * duration: timings persist inside the audit insert, so it can't time itself.
 */
const SCALE_MS = 30000;

interface Stage {
  nm: string;
  ms: number;
  startMs: number;
  color: string;
}

const STAGES: Stage[] = [
  { nm: "idempotency check", ms: 127, startMs: 0, color: "var(--cyan)" },
  { nm: "gmail fetch", ms: 191, startMs: 127, color: "var(--cyan)" },
  { nm: "classify (sonnet)", ms: 5990, startMs: 318, color: "var(--cyan)" },
  { nm: "apply label", ms: 579, startMs: 6308, color: "var(--cyan)" },
  { nm: "parse email", ms: 265, startMs: 6930, color: "var(--violet)" },
  { nm: "sublabel (opus)", ms: 3160, startMs: 7195, color: "var(--violet)" },
  { nm: "apply sublabel", ms: 522, startMs: 10355, color: "var(--violet)" },
  { nm: "notion match", ms: 12751, startMs: 10877, color: "var(--violet)" },
  { nm: "extract (opus)", ms: 4013, startMs: 23628, color: "var(--violet)" },
  { nm: "notion write", ms: 1245, startMs: 27641, color: "var(--violet)" },
];

function pct(ms: number): string {
  return `${((ms / SCALE_MS) * 100).toFixed(1)}%`;
}

function fmtMs(ms: number): string {
  return `${ms.toLocaleString("en-US").replace(/,/g, " ")} ms`;
}

export function TimingFig() {
  return (
    <div className="panel figure reveal">
      <i className="corner c1" />
      <i className="corner c2" />
      <i className="corner c3" />
      <i className="corner c4" />
      <div className="timing">
        {STAGES.map((s, i) => (
          <div className="t-row" key={s.nm}>
            <span className="nm">{s.nm}</span>
            <div className="t-track">
              <div
                className="t-bar"
                style={
                  {
                    "--bc": s.color,
                    left: pct(s.startMs),
                    // 0.5% floor keeps sub-150ms stages visible as more than a hairline
                    width: `max(${pct(s.ms)}, 0.5%)`,
                    "--dd": `${(i + 1) / 10}s`,
                  }
                }
              />
            </div>
            <span className="ms">{fmtMs(s.ms)}</span>
          </div>
        ))}
        <div className="t-axis">
          <span />
          <div className="ticks">
            <span>0</span>
            <span>10 s</span>
            <span>20 s</span>
            <span>≈30 s</span>
          </div>
          <span />
        </div>
      </div>
      <div className="t-foot">
        <span>
          <b style={{ color: "var(--cyan)" }}>■</b>
          {" triager   "}
          <b style={{ color: "var(--violet)" }}>■</b>
          {" job-apps graph"}
        </span>
        <span>
          stage medians, 805 + 14 production runs · label <b>≈7 s</b> · full job-app pipeline{" "}
          <b>≈29 s</b>
        </span>
      </div>
    </div>
  );
}
