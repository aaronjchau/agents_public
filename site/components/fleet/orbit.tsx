/** Hero orbital: two event-driven agents on the inner ring, three crons on the outer, Neon at center. */
export function Orbit() {
  return (
    <div
      className="orbit-box reveal anim-scope"
      role="img"
      aria-label="Orbital view: Triager and Job Apps on the inner event-driven ring. News Brief, Morning Brief and Spend Sync on the outer cron ring. Neon Postgres at the center."
    >
      <div className="orbit" aria-hidden="true">
        <div className="ring r1" />
        <div className="ring r2" />
        <div className="core">
          <i />
          <i />
          <b>Neon</b>
          <span>AUDIT LEDGER</span>
        </div>

        <div className="spin">
          <div
            className="sat"
            style={{ left: 364, top: 230, "--ac": "var(--cyan)" }}
          >
            <div className="chip">
              <span className="led" />
              <div>
                <b>Triager</b>
                <br />
                <span>every email</span>
              </div>
            </div>
          </div>
          <div
            className="sat"
            style={{ left: 96, top: 230, "--ac": "var(--violet)" }}
          >
            <div className="chip">
              <span className="led" />
              <div>
                <b>Job Apps</b>
                <br />
                <span>on dispatch</span>
              </div>
            </div>
          </div>
        </div>

        <div className="spin slow">
          <div
            className="sat"
            style={{ left: 230, top: 18, "--ac": "var(--orange)" }}
          >
            <div className="chip">
              <span className="led" />
              <div>
                <b>News Brief</b>
                <br />
                <span>09:15 ET</span>
              </div>
            </div>
          </div>
          <div
            className="sat"
            style={{ left: 413, top: 336, "--ac": "var(--yellow)" }}
          >
            <div className="chip">
              <span className="led" />
              <div>
                <b>Morning Brief</b>
                <br />
                <span>10:30 ET</span>
              </div>
            </div>
          </div>
          <div
            className="sat"
            style={{ left: 47, top: 336, "--ac": "var(--green)" }}
          >
            <div className="chip">
              <span className="led" />
              <div>
                <b>Spend Sync</b>
                <br />
                <span>04:00 ET</span>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
