/** Four publishing-discipline guarantee tiles. */
export function Guarantees() {
  return (
    <div className="rails">
      <div className="rail compact reveal" style={{ "--gc": "var(--yellow)" }}>
        <div className="glyph">1×</div>
        <b>One page per day</b>
        <p>The publisher checks for an existing page first. The same brief never posts twice.</p>
      </div>
      <div className="rail compact reveal" style={{ "--gc": "var(--green)" }}>
        <div className="glyph">◌</div>
        <b>Graceful degradation</b>
        <p>If a source is down, its section is skipped. The rest still publishes.</p>
      </div>
      <div className="rail compact reveal" style={{ "--gc": "var(--cyan)" }}>
        <div className="glyph">[N]</div>
        <b>No raw URLs</b>
        <p>The model only sees numbered link ids, which blocks prompt injection and fake links.</p>
      </div>
      <div className="rail compact reveal" style={{ "--gc": "var(--violet)" }}>
        <div className="glyph">✓</div>
        <b>Allowlisted citations</b>
        <p>Every link is verified against the fetched sources.</p>
      </div>
    </div>
  );
}
