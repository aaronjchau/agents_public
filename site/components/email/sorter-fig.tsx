/** sankey of the triager fanning email into the 12-label column. */
import { Fragment } from "react";

interface Ribbon {
  id?: string;
  d: string;
  delay: string;
  stroke: string;
  strokeOpacity: string;
  strokeWidth: string;
}

// ribbons: source stack tiles the Triager node's height (y 160-236) → label slots;
// widths ∝ share. Geometry is share-derived (cumulative sums), so values are stored verbatim.
const RIBBONS: Ribbon[] = [
  { id: "rib-notif", delay: ".0s", d: "M254,172.2 C470,172.2 540,72.4 700,72.4", stroke: "var(--amber)", strokeOpacity: ".40", strokeWidth: "12.5" },
  { id: "rib-news", delay: ".06s", d: "M254,183.7 C470,183.7 540,101.6 700,101.6", stroke: "var(--orange)", strokeOpacity: ".75", strokeWidth: "10.3" },
  { delay: ".12s", d: "M254,193.4 C470,193.4 540,128.5 700,128.5", stroke: "var(--amber)", strokeOpacity: ".34", strokeWidth: "9.1" },
  { id: "rib-jobs", delay: ".18s", d: "M254,201.1 C470,201.1 540,152.8 700,152.8", stroke: "var(--violet)", strokeOpacity: ".85", strokeWidth: "6.3" },
  { delay: ".24s", d: "M254,206.5 C470,206.5 540,174.1 700,174.1", stroke: "var(--amber)", strokeOpacity: ".30", strokeWidth: "4.6" },
  { delay: ".30s", d: "M254,210.8 C470,210.8 540,193.8 700,193.8", stroke: "var(--amber)", strokeOpacity: ".28", strokeWidth: "4" },
  { delay: ".36s", d: "M254,214.5 C470,214.5 540,212.7 700,212.7", stroke: "var(--amber)", strokeOpacity: ".26", strokeWidth: "3.4" },
  { delay: ".42s", d: "M254,217.3 C470,217.3 540,230.5 700,230.5", stroke: "var(--red)", strokeOpacity: ".45", strokeWidth: "2.2" },
  { delay: ".48s", d: "M254,219.2 C470,219.2 540,247.2 700,247.2", stroke: "var(--amber)", strokeOpacity: ".24", strokeWidth: "1.7" },
  { delay: ".54s", d: "M254,220.7 C470,220.7 540,263.1 700,263.1", stroke: "var(--amber)", strokeOpacity: ".22", strokeWidth: "1.1" },
  { delay: ".60s", d: "M254,221.8 C470,221.8 540,278.6 700,278.6", stroke: "var(--amber)", strokeOpacity: ".22", strokeWidth: "1.1" },
  { delay: ".66s", d: "M254,222.7 C470,222.7 540,293.9 700,293.9", stroke: "var(--amber)", strokeOpacity: ".20", strokeWidth: "0.9" },
];

interface Label {
  className: string;
  delay: string;
  y: string;
  text: string;
  pct: string;
  fill?: string;
}

// destination labels: shared y/delay drive both the name and its percent share.
const LABELS: Label[] = [
  { className: "lbl", delay: ".1s", y: "76.4", text: "Notifications", pct: "22%" },
  { className: "lbl", delay: ".16s", y: "105.6", text: "News → briefs", pct: "18%", fill: "var(--orange)" },
  { className: "lbl", delay: ".22s", y: "132.5", text: "Marketing", pct: "16%" },
  { className: "lbl", delay: ".28s", y: "156.8", text: "Job Apps → pipeline", pct: "11%", fill: "var(--violet)" },
  { className: "lbl sm", delay: ".34s", y: "178.1", text: "Finance", pct: "8%" },
  { className: "lbl sm", delay: ".40s", y: "197.8", text: "Purchases", pct: "7%" },
  { className: "lbl sm", delay: ".46s", y: "216.7", text: "People", pct: "6%" },
  { className: "lbl sm", delay: ".52s", y: "234.5", text: "Security", pct: "4%", fill: "var(--red)" },
  { className: "lbl sm", delay: ".58s", y: "251.2", text: "Networking", pct: "3%" },
  { className: "lbl sm", delay: ".64s", y: "267.1", text: "Medical", pct: "2%" },
  { className: "lbl sm", delay: ".70s", y: "282.6", text: "Home", pct: "2%" },
  { className: "lbl sm", delay: ".76s", y: "297.9", text: "Returns", pct: "1%" },
];

export function SorterFig() {
  return (
    <div className="panel figure reveal anim-scope">
      <i className="corner c1" />
      <i className="corner c2" />
      <i className="corner c3" />
      <i className="corner c4" />
      <div className="schematic">
        <div className="fig-canvas">
        <svg viewBox="0 0 940 400" role="img" aria-labelledby="fig3-title">
          <title id="fig3-title">Triager label fan-out</title>
          <desc>
            The Triager sorts each inbound email into exactly one of 12 labels, with ribbon width
            proportional to each label&apos;s share of email and a flagged overlay added only when
            action is required.
          </desc>
          {/* ribbons: source stack tiles the Triager node's height (y 160-236) → label slots; widths ∝ share */}
          {RIBBONS.map((r) => (
            <path
              key={r.delay}
              id={r.id}
              className="rib"
              pathLength="1"
              style={{ "--d": r.delay }}
              d={r.d}
              stroke={r.stroke}
              strokeOpacity={r.strokeOpacity}
              strokeWidth={r.strokeWidth}
            />
          ))}

          {/* source node, drawn over ribbon ends */}
          <rect
            x="40"
            y="160"
            width="212"
            height="76"
            rx="10"
            fill="var(--panel-2)"
            stroke="var(--amber)"
            strokeWidth="1.4"
            pathLength="1"
            style={{ "--d": ".1s" }}
          />
          <circle
            className="n-dot"
            style={{ "--d": ".1s" }}
            cx="62"
            cy="186"
            fill="var(--cyan)"
          />
          <text className="s-title" style={{ "--d": ".1s" }} x="76" y="191">
            Triager
          </text>
          <text className="s-sub" style={{ "--d": ".1s" }} x="62" y="210">
            ONE CALL PER EMAIL
          </text>
          <text className="s-sub" style={{ "--d": ".1s" }} x="62" y="224">
            GMAIL · PUB/SUB · SONNET
          </text>

          {/* column header above the 12 destination labels */}
          <rect
            x="702"
            y="18"
            width="204"
            height="32"
            rx="8"
            fill="var(--panel-2)"
            stroke="var(--line-2)"
            strokeWidth="1"
            pathLength="1"
            style={{ "--d": ".05s" }}
          />
          <text
            className="s-title"
            style={{ "--d": ".05s" }}
            x="804"
            y="39"
            fontSize="12.5"
            textAnchor="middle"
          >
            Gmail labels
          </text>

          {LABELS.map((l) => (
            <Fragment key={l.delay}>
              <text className={l.className} style={{ "--d": l.delay }} x="712" y={l.y} fill={l.fill}>
                {l.text}
              </text>
              <text
                className="lbl-pct"
                style={{ "--d": l.delay }}
                x="898"
                y={l.y}
                textAnchor="end"
              >
                {l.pct}
              </text>
            </Fragment>
          ))}

          <rect
            x="40"
            y="330"
            width="212"
            height="44"
            rx="8"
            fill="none"
            stroke="var(--amber)"
            strokeWidth="0.9"
            strokeDasharray="4 5"
            pathLength="1"
            style={{ "--d": ".8s" }}
          />
          <text className="s-note" style={{ "--d": ".8s" }} x="58" y="350">
            ⚠ FLAGGED · ADDITIVE OVERLAY
          </text>
          <text className="s-sub" style={{ "--d": ".8s" }} x="58" y="364">
            ONLY WHEN ACTION IS REQUIRED
          </text>
        </svg>
        <div className="pk-layer" style={{ height: 400 }}>
          <i className="pk" style={{ "--pc": "var(--violet)", "--pk": "pk-sort-a", "--dur": "3.6s", "--dl": "1.2s" }} />
          <i className="pk" style={{ "--pc": "var(--orange)", "--pk": "pk-sort-b", "--dur": "3.6s", "--dl": "2.6s" }} />
        </div>
        </div>
      </div>
    </div>
  );
}
