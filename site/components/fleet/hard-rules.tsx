/** The four invariants enforced in code and covered by tests. */
export function HardRules() {
  return (
    <div className="rails">
      <div className="rail reveal" style={{ "--gc": "var(--red)" }}>
        <div className="glyph">✕</div>
        <b>Never sends</b>
        <p>No automated send path exists. Replies are human, every time.</p>
      </div>
      <div className="rail reveal" style={{ "--gc": "var(--violet)" }}>
        <div className="glyph">↑</div>
        <b>Never regresses</b>
        <p>Saved → Applied → Screen → Interview → Offer. One direction only.</p>
      </div>
      <div className="rail reveal" style={{ "--gc": "var(--yellow)" }}>
        <div className="glyph">∅</div>
        <b>Never fabricates</b>
        <p>A date the email doesn&apos;t state stays null in Notion.</p>
      </div>
      <div className="rail reveal" style={{ "--gc": "var(--green)" }}>
        <div className="glyph">▣</div>
        <b>Always audited</b>
        <p>Tokens, cost, latency, path taken: one row per run.</p>
      </div>
    </div>
  );
}
