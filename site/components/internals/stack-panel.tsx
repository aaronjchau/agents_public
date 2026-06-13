/** The stack as pill groups. */
// group headings are h3 for outline order; inline style mirrors the h4-scoped .pills rule in globals.css
const groupHead: React.CSSProperties = {
  fontFamily: "var(--mono)",
  fontSize: 10,
  letterSpacing: "0.16em",
  textTransform: "uppercase",
  color: "var(--faint)",
  margin: "18px 0 9px",
};

export function StackPanel() {
  return (
    <div className="panel reveal pills">
      <h3 style={{ ...groupHead, marginTop: 0 }}>Backend</h3>
      <div className="pill-row">
        <code>Python 3.12</code>
        <code>FastAPI</code>
        <code>SQLAlchemy 2.0</code>
        <code>Alembic</code>
        <code>LangGraph</code>
      </div>
      <h3 style={groupHead}>Frontend</h3>
      <div className="pill-row">
        <code>Next.js 16</code>
        <code>React 19</code>
        <code>Tailwind v4</code>
      </div>
      <h3 style={groupHead}>Data &amp; models</h3>
      <div className="pill-row">
        <code>Neon Postgres</code>
        <code>JSONB timings</code>
        <code>Sonnet 4.6</code>
        <code>Opus 4.7</code>
        <code>prompt caching</code>
      </div>
      <h3 style={groupHead}>Ops</h3>
      <div className="pill-row">
        <code>
          Modal <span>apps + crons</span>
        </code>
        <code>
          LangSmith <span>tracing</span>
        </code>
        <code>
          Pub/Sub <span>push</span>
        </code>
        <code>
          1Password <span>secrets</span>
        </code>
      </div>
    </div>
  );
}
