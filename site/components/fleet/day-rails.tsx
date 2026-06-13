/** 24h timeline: event-driven lanes plus cron markers, swept by the playhead. */

/* one tick per inbound email (32/day: the 30-day production average); --pd = left% × 14s sweep */
const TRIAGER_TICKS = [
  { left: "11.3%", pd: "1.58s" },
  { left: "14.0%", pd: "1.96s" },
  { left: "23.1%", pd: "3.23s" },
  { left: "27.3%", pd: "3.82s" },
  { left: "31.3%", pd: "4.39s" },
  { left: "32.3%", pd: "4.53s" },
  { left: "32.9%", pd: "4.6s" },
  { left: "34.9%", pd: "4.89s" },
  { left: "38.0%", pd: "5.32s" },
  { left: "38.7%", pd: "5.41s" },
  { left: "41.3%", pd: "5.78s" },
  { left: "46.2%", pd: "6.46s" },
  { left: "47.6%", pd: "6.67s" },
  { left: "49.8%", pd: "6.97s" },
  { left: "50.2%", pd: "7.03s" },
  { left: "50.7%", pd: "7.1s" },
  { left: "52.0%", pd: "7.28s" },
  { left: "56.6%", pd: "7.92s" },
  { left: "57.7%", pd: "8.08s" },
  { left: "58.2%", pd: "8.14s" },
  { left: "62.9%", pd: "8.8s" },
  { left: "68.0%", pd: "9.52s" },
  { left: "68.3%", pd: "9.56s" },
  { left: "69.0%", pd: "9.66s" },
  { left: "70.5%", pd: "9.87s" },
  { left: "78.5%", pd: "10.99s" },
  { left: "80.7%", pd: "11.3s" },
  { left: "81.2%", pd: "11.37s" },
  { left: "83.3%", pd: "11.66s" },
  { left: "96.3%", pd: "13.49s" },
  { left: "98.0%", pd: "13.72s" },
  { left: "99.9%", pd: "13.99s" },
];

export function DayRails() {
  return (
    <div className="panel day reveal anim-scope">
      <div className="axis">
        <span>00:00</span>
        <span>04:00</span>
        <span>08:00</span>
        <span>12:00</span>
        <span>16:00</span>
        <span>20:00</span>
        <span>24:00</span>
      </div>
      <div className="lane">
        <span className="nm">triager</span>
        <div className="track">
          {TRIAGER_TICKS.map((t) => (
            <span
              key={`${t.left}${t.pd}`}
              className="tick-ev"
              style={{ "--bc": "var(--cyan)", left: t.left, "--pd": t.pd }}
            />
          ))}
          <em className="lane-note">EVENT-DRIVEN · ~32 EMAILS / DAY</em>
        </div>
      </div>
      <div className="lane">
        <span className="nm">job-apps</span>
        <div className="track">
          <span
            className="tick-ev"
            style={
              { "--bc": "var(--violet)", left: "45.8%", "--pd": "6.42s" }
            }
          />
          <em className="lane-note">FIRES ON JOB-APP EMAIL</em>
        </div>
      </div>
      <div className="lane">
        <span className="nm">spend-sync</span>
        <div className="track">
          <span
            className="marker"
            style={{ "--bc": "var(--green)", left: "16.7%", "--pd": "2.3s" }}
          >
            <i />
            <em>04:00</em>
          </span>
        </div>
      </div>
      <div className="lane">
        <span className="nm">news-brief</span>
        <div className="track">
          <span
            className="marker"
            style={
              { "--bc": "var(--orange)", left: "38.5%", "--pd": "5.4s" }
            }
          >
            <i />
            <em>09:15</em>
          </span>
        </div>
      </div>
      <div className="lane">
        <span className="nm">morning-brief</span>
        <div className="track">
          <span
            className="marker"
            style={
              { "--bc": "var(--yellow)", left: "43.7%", "--pd": "6.1s" }
            }
          >
            <i />
            <em>10:30</em>
          </span>
        </div>
      </div>
      <div className="ph-strip">
        <div className="ph-line" />
      </div>
    </div>
  );
}
