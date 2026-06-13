/** dispatch→Notion stages and the match cascade funnel. */
const panelHead: React.CSSProperties = { fontSize: 16, fontWeight: 800, letterSpacing: "-0.025em" };

export function PipelinePanels() {
  return (
    <div className="grid-2">
      <div className="panel reveal">
        <div className="sec-head" style={{ marginBottom: 14 }}>
          {/* h3 keeps heading order under the section h2; weight + tracking mirror the h2-scoped .sec-head rule */}
          <h3 style={panelHead}>Dispatch → Notion</h3>
          <span className="note">5 stages</span>
        </div>
        <div className="vsteps">
          <div className="vstep">
            <b>Dispatch</b>
            <span>BEARER POST FROM TRIAGER</span>
          </div>
          <div className="vstep">
            <b>Classify</b>
            <span>ONE OF 7 SUBLABELS</span>
          </div>
          <div className="vstep">
            <b>Sublabel</b>
            <span>APPLIED IN GMAIL</span>
          </div>
          <div className="vstep">
            <b>Match</b>
            <span>CASCADE → NOTION ROW</span>
          </div>
          <div className="vstep">
            <b>Status ↑</b>
            <span>NEVER BACKWARD</span>
          </div>
        </div>
        <div className="sub-chips">
          <code>Offer</code>
          <code>Interview Scheduling</code>
          <code>Assessment</code>
          <code>Recruiter Outreach</code>
          <code>Status Update</code>
          <code>Application Confirmation</code>
          <code>Rejection</code>
        </div>
      </div>

      <div className="panel reveal">
        <div className="sec-head" style={{ marginBottom: 14 }}>
          <h3 style={panelHead}>The match cascade</h3>
          <span className="note">never guesses</span>
        </div>
        <div className="funnel">
          <div
            className="fun-bar"
            style={{ "--fw": "100%", "--fd": ".05s" }}
          >
            <b>Posting URL</b>
            <span>EXACT MATCH</span>
          </div>
          <div className="fun-arrow">no match ↓</div>
          <div
            className="fun-bar"
            style={{ "--fw": "74%", "--fd": ".25s" }}
          >
            <b>Company + role</b>
            <span>VERIFIED AGAINST THE ROW</span>
          </div>
          <div className="fun-arrow">still ambiguous ↓</div>
          <div
            className="fun-bar warn"
            style={{ "--fw": "50%", "--fd": ".45s" }}
          >
            <b>⚠ Human review</b>
            <span>NEVER WRITES TO A GUESSED ROW</span>
          </div>
        </div>
      </div>
    </div>
  );
}
