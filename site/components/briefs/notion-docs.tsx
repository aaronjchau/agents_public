/** Side-by-side publisher-enforced format rules and the Notion output preview. */
export function NotionDocs() {
  return (
    <div className="docs-2 reveal">
      <div className="doc">
        <div className="dt">Format rules</div>
        <div className="meta">ENFORCED BY THE PUBLISHER, NOT THE PROMPT</div>
        <div className="q">
          One <b>quote-block line per story</b>: label, sentence, source link.
        </div>
        <div className="q">
          Citation URLs are <b>allowlisted</b> against the source markdown. No invented links.
        </div>
        <div className="q">
          <b>$ is escaped</b>. Notion would render math.
        </div>
        <div className="q">
          A failing source <b>degrades its section</b>, never the page.
        </div>
      </div>
      <div className="doc">
        <div className="dt">📰 News — Thu</div>
        <div className="meta">NEWS BRIEF · GROUPED BY CATEGORY</div>
        <span className="h">AI &amp; Tech</span>
        <div className="q">
          <b>Frontier models:</b> Lab ships a new reasoning model. —{" "}
          <span className="src-link">The Information</span>
        </div>
        <div className="q">
          <b>Chips:</b> Fab capacity tightens again. — <span className="src-link">Bloomberg</span>
        </div>
        <span className="h">Markets</span>
        <div className="q">
          <b>Macro:</b> Yields drift ahead of CPI print. —{" "}
          <span className="src-link">Reuters</span>
        </div>
      </div>
    </div>
  );
}
