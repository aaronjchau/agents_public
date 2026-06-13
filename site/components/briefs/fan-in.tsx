/** Morning Brief fan-in: five sources converge into one Notion page preview. */
export function FanIn() {
  return (
    <div className="panel figure reveal">
      <i className="corner c1" />
      <i className="corner c2" />
      <i className="corner c3" />
      <i className="corner c4" />
      <div className="fan">
        <div className="fan-src">
          <div className="src">
            <b>Tasks</b>
            <span>NOTION</span>
          </div>
          <div className="src">
            <b>Focus hours</b>
            <span>NOTION</span>
          </div>
          <div className="src">
            <b>LeetCode stats</b>
            <span>NOTION</span>
          </div>
          <div className="src">
            <b>Flagged email</b>
            <span>GMAIL</span>
          </div>
          <div className="src">
            <b>Events</b>
            <span>CALENDAR</span>
          </div>
        </div>
        <div className="fan-mid" aria-hidden="true">
          <svg viewBox="0 0 90 230">
            <path
              className="draw dash"
              pathLength="1"
              style={{ "--d": ".1s" }}
              d="M2,25 C50,25 50,115 88,115"
            />
            <path
              className="draw dash"
              pathLength="1"
              style={{ "--d": ".2s" }}
              d="M2,70 C50,70 50,115 88,115"
            />
            <path
              className="draw dash"
              pathLength="1"
              style={{ "--d": ".3s" }}
              d="M2,115 H88"
            />
            <path
              className="draw dash"
              pathLength="1"
              style={{ "--d": ".4s" }}
              d="M2,160 C50,160 50,115 88,115"
            />
            <path
              className="draw dash"
              pathLength="1"
              style={{ "--d": ".5s" }}
              d="M2,205 C50,205 50,115 88,115"
            />
          </svg>
        </div>
        <div className="fan-out">
          <div className="doc">
            <div className="dt">🌅 Morning Brief — Thu</div>
            <div className="q">
              <b>TL;DR:</b> 3 tasks due today, 1 flagged email needs a reply, light calendar.
            </div>
            <span className="h">Today (3)</span>
            <div className="task">
              <i>☐</i> Finish the review draft
            </div>
            <div className="task">
              <i>☐</i> 2 LeetCode problems
            </div>
            <div className="task">
              <i>☐</i> Reply to the flagged thread
            </div>
            <span className="h">Calendar</span>
            <div className="task">
              <i>·</i> 10:00 team call · 18:00 gym
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
