/** subway map of the monotonic status line with a terminal siding. */
interface Station {
  label: string;
  cx: number;
  d: string;
  r: number;
  stroke: string;
  strokeWidth: string;
  textY: number;
  fill?: string;
}

// main-line stations: cx steps by 180; Offer is the larger amber terminus.
const STATIONS: Station[] = [
  { label: "Saved", cx: 100, d: ".1s", r: 9, stroke: "var(--violet)", strokeWidth: "3", textY: 42 },
  { label: "Applied", cx: 280, d: ".22s", r: 9, stroke: "var(--violet)", strokeWidth: "3", textY: 42 },
  { label: "Screen", cx: 460, d: ".34s", r: 9, stroke: "var(--violet)", strokeWidth: "3", textY: 42 },
  { label: "Interview", cx: 640, d: ".46s", r: 9, stroke: "var(--violet)", strokeWidth: "3", textY: 42 },
  {
    label: "Offer",
    cx: 820,
    d: ".58s",
    r: 10,
    stroke: "var(--amber)",
    strokeWidth: "3.5",
    textY: 40,
    fill: "var(--amber)",
  },
];

interface Siding {
  label: string;
  cx: number;
  d: string;
}

// read-only terminal siding nodes: cx steps by 100.
const SIDINGS: Siding[] = [
  { label: "REJECTED", cx: 600, d: ".9s" },
  { label: "WITHDRAWN", cx: 700, d: "1s" },
  { label: "ARCHIVED", cx: 800, d: "1.1s" },
];

export function StatusLineFig() {
  return (
    <div className="panel figure reveal">
      <i className="corner c1" />
      <i className="corner c2" />
      <i className="corner c3" />
      <i className="corner c4" />
      <div className="schematic">
        <svg viewBox="0 0 940 215" role="img" aria-labelledby="fig4-title">
          <title id="fig4-title">Job application status line</title>
          <desc>
            Application status moves one way from Saved through Applied, Screen, and Interview to
            Offer, with Rejected, Withdrawn, and Archived on a read-only terminal siding.
          </desc>
          <path
            className="draw"
            pathLength="1"
            style={{ "--d": ".0s" }}
            d="M60,70 H868"
            stroke="var(--violet)"
            strokeWidth="3"
          />
          <path
            pathLength="1"
            style={{ "--d": ".7s" }}
            d="M868,63 L884,70 L868,77 Z"
            fill="var(--violet)"
            stroke="var(--violet)"
          />
          <path
            className="draw"
            pathLength="1"
            style={{ "--d": ".55s" }}
            d="M186,64 l9,6 -9,6 M366,64 l9,6 -9,6 M546,64 l9,6 -9,6 M726,64 l9,6 -9,6"
            stroke="var(--faint)"
            strokeWidth="1.6"
          />
          {STATIONS.map((s) => (
            <circle
              key={s.label}
              pathLength="1"
              style={{ "--d": s.d }}
              cx={s.cx}
              cy="70"
              r={s.r}
              fill="var(--bg)"
              stroke={s.stroke}
              strokeWidth={s.strokeWidth}
            />
          ))}
          {STATIONS.map((s) => (
            <text
              key={s.label}
              className="s-title"
              style={{ "--d": s.d }}
              x={s.cx}
              y={s.textY}
              textAnchor="middle"
              fill={s.fill}
            >
              {s.label}
            </text>
          ))}
          <text
            className="s-sub"
            style={{ "--d": ".7s" }}
            x="884"
            y="94"
            textAnchor="end"
          >
            ONE WAY: AN EMAIL NEVER MOVES A ROW LEFT
          </text>

          <path
            className="draw dash"
            pathLength="1"
            style={{ "--d": ".8s" }}
            d="M460,70 C460,118 505,140 560,140 H810"
            stroke="var(--red)"
            strokeWidth="2"
          />
          {SIDINGS.map((s) => (
            <circle
              key={s.label}
              pathLength="1"
              style={{ "--d": s.d }}
              cx={s.cx}
              cy="140"
              r="7"
              fill="var(--bg)"
              stroke="var(--red)"
              strokeWidth="2"
              strokeDasharray="3 3"
            />
          ))}
          {SIDINGS.map((s) => (
            <text
              key={s.label}
              className="s-sub"
              style={{ "--d": s.d }}
              x={s.cx}
              y="170"
              textAnchor="middle"
              fill="var(--red)"
            >
              {s.label}
            </text>
          ))}
          <text className="s-sub" style={{ "--d": ".8s" }} x="478" y="122">
            TERMINAL · READ-ONLY
          </text>
        </svg>
      </div>
    </div>
  );
}
