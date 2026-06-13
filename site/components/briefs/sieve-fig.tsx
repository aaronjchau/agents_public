/** News Brief sieve: tiered streams from news email into one Notion page. */
export function SieveFig() {
  return (
    <div className="panel figure reveal anim-scope">
      <i className="corner c1" />
      <i className="corner c2" />
      <i className="corner c3" />
      <i className="corner c4" />
      <div className="schematic">
        <div className="fig-canvas">
        <svg viewBox="0 0 940 310" role="img" aria-labelledby="fig5-title">
          <title id="fig5-title">News Brief sieve</title>
          <desc>
            News email from the last 24 hours is sorted into always, sometimes, and never tiers,
            and the kept stories are merged into one Notion page while the rest are dropped.
          </desc>
          {/* stream widths ∝ how much email hits each tier */}
          <path
            className="rib"
            pathLength="1"
            style={{ "--d": ".15s" }}
            d="M220,128 C280,128 280,75 332,75"
            stroke="var(--green)"
            strokeOpacity=".55"
            strokeWidth="10"
          />
          <path
            className="rib"
            pathLength="1"
            style={{ "--d": ".25s" }}
            d="M220,140 C285,140 285,149 332,149"
            stroke="var(--yellow)"
            strokeOpacity=".5"
            strokeWidth="7"
          />
          <path
            className="rib"
            pathLength="1"
            style={{ "--d": ".35s" }}
            d="M220,152 C280,152 280,223 332,223"
            stroke="var(--red)"
            strokeOpacity=".45"
            strokeWidth="15"
          />
          <path
            className="rib"
            pathLength="1"
            style={{ "--d": ".55s" }}
            d="M628,75 C690,75 690,110 742,110"
            stroke="var(--green)"
            strokeOpacity=".55"
            strokeWidth="10"
          />
          <path
            className="rib"
            pathLength="1"
            style={{ "--d": ".65s" }}
            d="M628,149 C690,149 690,128 742,128"
            stroke="var(--yellow)"
            strokeOpacity=".5"
            strokeWidth="5"
            strokeDasharray="5 6"
          />
          <path
            className="rib"
            pathLength="1"
            style={{ "--d": ".75s" }}
            d="M628,223 H700"
            stroke="var(--red)"
            strokeOpacity=".45"
            strokeWidth="15"
          />

          <rect
            pathLength="1"
            style={{ "--d": ".0s" }}
            x="40"
            y="105"
            width="180"
            height="70"
            rx="10"
            fill="var(--panel-2)"
            stroke="var(--orange)"
            strokeWidth="1.4"
          />
          <circle
            className="n-dot"
            style={{ "--d": ".0s" }}
            cx="62"
            cy="130"
            fill="var(--orange)"
          />
          <text className="s-title" style={{ "--d": ".0s" }} x="76" y="135">
            News email
          </text>
          <text className="s-sub" style={{ "--d": ".0s" }} x="62" y="154">
            LAST 24H · TRACKING STRIPPED
          </text>

          <rect
            pathLength="1"
            style={{ "--d": ".4s" }}
            x="332"
            y="52"
            width="296"
            height="46"
            rx="9"
            fill="var(--panel-2)"
            stroke="var(--green)"
            strokeWidth="1.3"
          />
          <text
            className="s-title"
            style={{ "--d": ".4s" }}
            x="350"
            y="72"
            fontSize="11.5"
            fill="var(--green)"
          >
            ALWAYS
          </text>
          <text className="s-sub" style={{ "--d": ".4s" }} x="350" y="87">
            FRONTIER AI · LEADERSHIP · REGULATION
          </text>
          <rect
            pathLength="1"
            style={{ "--d": ".5s" }}
            x="332"
            y="126"
            width="296"
            height="46"
            rx="9"
            fill="var(--panel-2)"
            stroke="var(--yellow)"
            strokeWidth="1.3"
          />
          <text
            className="s-title"
            style={{ "--d": ".5s" }}
            x="350"
            y="146"
            fontSize="11.5"
            fill="var(--yellow)"
          >
            SOMETIMES
          </text>
          <text className="s-sub" style={{ "--d": ".5s" }} x="350" y="161">
            MACRO &amp; MARKETS · BIG-TECH · GADGETS
          </text>
          <rect
            pathLength="1"
            style={{ "--d": ".6s" }}
            x="332"
            y="200"
            width="296"
            height="46"
            rx="9"
            fill="var(--panel-2)"
            stroke="var(--red)"
            strokeWidth="1.3"
          />
          <text
            className="s-title"
            style={{ "--d": ".6s" }}
            x="350"
            y="220"
            fontSize="11.5"
            fill="var(--red)"
          >
            NEVER
          </text>
          <text className="s-sub" style={{ "--d": ".6s" }} x="350" y="235">
            PROMOS · LIFESTYLE
          </text>

          <rect
            pathLength="1"
            style={{ "--d": ".8s" }}
            x="742"
            y="88"
            width="170"
            height="64"
            rx="10"
            fill="var(--panel-2)"
            stroke="var(--amber)"
            strokeWidth="1.4"
          />
          <text className="s-title" style={{ "--d": ".8s" }} x="760" y="113">
            One Notion page
          </text>
          <text className="s-sub" style={{ "--d": ".8s" }} x="760" y="131">
            GROUPED BY CATEGORY
          </text>
          <path
            className="draw"
            pathLength="1"
            style={{ "--d": ".85s" }}
            d="M712,215 l16,16 M728,215 l-16,16"
            stroke="var(--red)"
            strokeWidth="2"
          />
          <text
            className="s-sub"
            style={{ "--d": ".85s" }}
            x="745"
            y="230"
            fill="var(--red)"
          >
            DROPPED
          </text>
        </svg>
        <div className="pk-layer" style={{ height: 310 }}>
          <i className="pk" style={{ "--pc": "var(--green)", "--pk": "pk-sieve-a", "--dur": "2.4s", "--dl": "1.5s" }} />
          <i className="pk" style={{ "--pc": "var(--red)", "--pk": "pk-sieve-b", "--dur": "2s", "--dl": "3s" }} />
        </div>
        </div>
      </div>
    </div>
  );
}
