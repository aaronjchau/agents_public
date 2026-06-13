/** FIG. 1 system elevation: full pipeline schematic with packet-flow overlays. */
export function SystemFig() {
  return (
    <div className="panel figure reveal anim-scope">
      <i className="corner c1" />
      <i className="corner c2" />
      <i className="corner c3" />
      <i className="corner c4" />
      <div className="schematic">
        <div className="fig-canvas">
        <svg viewBox="0 0 940 470" role="img" aria-labelledby="fig1-title">
          <title id="fig1-title">Fleet system schematic</title>
          <desc>
            Gmail events flow through the Triager into 12 labels and the Job Apps pipeline, two
            cron agents publish briefs to Notion, a third syncs spend, and every run lands in a
            Neon Postgres audit ledger read by a Next.js dashboard.
          </desc>
          <rect
            className="draw"
            pathLength="1"
            style={{ "--d": ".0s" }}
            x="30"
            y="50"
            width="160"
            height="56"
            rx="8"
          />
          <rect
            className="draw acc"
            pathLength="1"
            style={{ "--d": ".15s" }}
            x="360"
            y="50"
            width="180"
            height="56"
            rx="8"
          />
          <rect
            className="draw"
            pathLength="1"
            style={{ "--d": ".3s" }}
            x="710"
            y="56"
            width="150"
            height="44"
            rx="8"
          />
          <rect
            className="draw acc"
            pathLength="1"
            style={{ "--d": ".45s" }}
            x="360"
            y="196"
            width="180"
            height="56"
            rx="8"
          />
          <rect
            className="draw"
            pathLength="1"
            style={{ "--d": ".6s" }}
            x="710"
            y="202"
            width="150"
            height="44"
            rx="8"
          />
          <rect
            className="draw"
            pathLength="1"
            style={{ "--d": ".75s" }}
            x="360"
            y="342"
            width="200"
            height="56"
            rx="8"
          />
          <rect
            className="draw"
            pathLength="1"
            style={{ "--d": ".9s" }}
            x="710"
            y="382"
            width="180"
            height="56"
            rx="8"
          />
          {/* cron agents: dashed group box + one node each */}
          <rect
            className="draw dash"
            pathLength="1"
            style={{ "--d": ".95s" }}
            x="22"
            y="246"
            width="176"
            height="112"
            rx="10"
          />
          <rect
            className="draw"
            pathLength="1"
            style={{ "--d": "1s" }}
            x="30"
            y="254"
            width="160"
            height="42"
            rx="8"
          />
          <rect
            className="draw"
            pathLength="1"
            style={{ "--d": "1.05s" }}
            x="30"
            y="306"
            width="160"
            height="42"
            rx="8"
          />
          <rect
            className="draw"
            pathLength="1"
            style={{ "--d": "1.1s" }}
            x="30"
            y="398"
            width="160"
            height="42"
            rx="8"
          />

          <path
            className="draw dash acc"
            pathLength="1"
            style={{ "--d": ".2s" }}
            d="M190,78 H360 M450,106 V196 M450,252 V342"
          />
          <path
            className="draw dash"
            pathLength="1"
            style={{ "--d": ".4s" }}
            d="M540,78 H710"
          />
          <path
            className="draw dash"
            pathLength="1"
            style={{ "--d": ".55s" }}
            d="M540,224 H710"
          />
          <path
            className="draw dash"
            pathLength="1"
            style={{ "--d": ".8s" }}
            d="M560,370 C660,370 660,410 710,410"
          />
          {/* gmail → briefs · briefs publish → notion · briefs + spend audit → neon */}
          <path
            className="draw dash"
            pathLength="1"
            style={{ "--d": ".9s" }}
            d="M110,106 V246"
          />
          {/* publish path hugs the left, bottom, and right edges to avoid every crossing */}
          <path
            className="draw dash"
            pathLength="1"
            style={{ "--d": "1.15s", stroke: "var(--orange)" }}
            d="M22,301 H14 Q10,301 10,309 V450 Q10,458 18,458 H897 Q905,458 905,450 V232 Q905,224 897,224 H860"
          />
          <path
            className="draw dash"
            pathLength="1"
            style={{ "--d": "1.1s" }}
            d="M198,330 C280,330 310,372 360,382"
          />
          <path
            className="draw dash"
            pathLength="1"
            style={{ "--d": "1.15s" }}
            d="M190,419 C270,419 310,400 360,392"
          />

          <path
            className="draw acc"
            pathLength="1"
            style={{ "--d": ".25s" }}
            d="M352,74 l8,4 -8,4 M446,188 l4,8 4,-8 M446,334 l4,8 4,-8"
          />

          <circle
            className="n-dot"
            style={{ "--d": ".0s" }}
            cx="50"
            cy="71"
            fill="var(--cyan)"
          />
          <text className="s-title" style={{ "--d": ".0s" }} x="64" y="76">
            Gmail
          </text>
          <text className="s-sub" style={{ "--d": ".0s" }} x="50" y="94">
            4 ACCTS · PUB/SUB WATCH
          </text>
          <circle
            className="n-dot"
            style={{ "--d": ".15s" }}
            cx="380"
            cy="71"
            fill="var(--cyan)"
          />
          <text className="s-title" style={{ "--d": ".15s" }} x="394" y="76">
            Triager
          </text>
          <text className="s-sub" style={{ "--d": ".15s" }} x="380" y="94">
            JWT WEBHOOK · SONNET
          </text>
          <text
            className="s-title"
            style={{ "--d": ".3s" }}
            x="730"
            y="77"
            fontSize="12.5"
          >
            12 labels
          </text>
          <text className="s-sub" style={{ "--d": ".3s" }} x="730" y="93">
            FLAG WHEN ACTIONABLE
          </text>
          <circle
            className="n-dot"
            style={{ "--d": ".45s" }}
            cx="380"
            cy="217"
            fill="var(--violet)"
          />
          <text
            className="s-title"
            style={{ "--d": ".45s" }}
            x="394"
            y="222"
          >
            Job Apps
          </text>
          <text className="s-sub" style={{ "--d": ".45s" }} x="380" y="240">
            LANGGRAPH · OPUS
          </text>
          <text
            className="s-title"
            style={{ "--d": ".6s" }}
            x="730"
            y="223"
            fontSize="12.5"
          >
            Notion
          </text>
          <text className="s-sub" style={{ "--d": ".6s" }} x="730" y="239">
            STATUS ↑ · BRIEF PAGES
          </text>
          <circle
            className="n-dot"
            style={{ "--d": ".75s" }}
            cx="380"
            cy="363"
            fill="var(--green)"
          />
          <text
            className="s-title"
            style={{ "--d": ".75s" }}
            x="394"
            y="368"
          >
            Neon Postgres
          </text>
          <text className="s-sub" style={{ "--d": ".75s" }} x="380" y="386">
            AUDIT LEDGER · RUNS · SPEND
          </text>
          <circle
            className="n-dot"
            style={{ "--d": ".9s" }}
            cx="730"
            cy="403"
            fill="var(--green)"
          />
          <text className="s-title" style={{ "--d": ".9s" }} x="744" y="408">
            Dashboard
          </text>
          <text className="s-sub" style={{ "--d": ".9s" }} x="730" y="426">
            NEXT.JS · DIRECT READS
          </text>
          <circle
            className="n-dot"
            style={{ "--d": "1s" }}
            cx="48"
            cy="270"
            fill="var(--orange)"
          />
          <text
            className="s-title"
            style={{ "--d": "1s" }}
            x="62"
            y="274"
            fontSize="12.5"
          >
            News Brief
          </text>
          <text className="s-sub" style={{ "--d": "1s" }} x="48" y="288">
            09:15 ET · CURATED NEWS
          </text>
          <circle
            className="n-dot"
            style={{ "--d": "1.05s" }}
            cx="48"
            cy="322"
            fill="var(--yellow)"
          />
          <text
            className="s-title"
            style={{ "--d": "1.05s" }}
            x="62"
            y="326"
            fontSize="12.5"
          >
            Morning Brief
          </text>
          <text className="s-sub" style={{ "--d": "1.05s" }} x="48" y="340">
            10:30 ET · TASKS + CAL
          </text>
          <circle
            className="n-dot"
            style={{ "--d": "1.1s" }}
            cx="48"
            cy="414"
            fill="var(--green)"
          />
          <text
            className="s-title"
            style={{ "--d": "1.1s" }}
            x="62"
            y="418"
            fontSize="12.5"
          >
            Spend Sync
          </text>
          <text className="s-sub" style={{ "--d": "1.1s" }} x="48" y="432">
            04:00 ET · BILL → NEON
          </text>
          <text
            className="s-dim"
            style={{ "--d": "1.15s", fill: "var(--orange)" }}
            x="500"
            y="450"
          >
            PUBLISH · 1 PAGE / DAY EACH
          </text>
          <text className="s-note" style={{ "--d": "1.1s" }} x="475" y="160">
            DISPATCH
          </text>
          <text className="s-note" style={{ "--d": "1.1s" }} x="475" y="306">
            AUDIT ROW
          </text>
          <text className="s-note" style={{ "--d": "1.1s" }} x="600" y="68">
            LABEL
          </text>
          <text className="s-note" style={{ "--d": "1.1s" }} x="596" y="214">
            STATUS ↑
          </text>
          <text className="s-note" style={{ "--d": "1.1s" }} x="610" y="358">
            SERVERLESS READ
          </text>
        </svg>
        <div className="pk-layer" style={{ height: 470 }}>
          <i className="pk" style={{ "--pc": "var(--amber)", "--pk": "pk-sys-a", "--dur": "5s", "--dl": "2s" }} />
          <i className="pk" style={{ "--pc": "var(--green)", "--pk": "pk-sys-b", "--dur": "3.4s", "--dl": "3s" }} />
          <i className="pk" style={{ "--pc": "var(--orange)", "--pk": "pk-sys-c", "--dur": "8s", "--dl": "3.8s" }} />
        </div>
        </div>
      </div>
    </div>
  );
}
