import { TEST_COUNT } from "@/lib/site";

/** The audit-row JSON and the repo tree it comes from. */
export function RunArtifacts() {
  return (
    <div className="grid-2">
      <div className="panel reveal json">
        {/* whitespace inside the pre is explicit strings; JSX would strip the newlines */}
        <pre style={{ margin: 0, fontFamily: "inherit" }}>
          {"{\n  "}
          <span className="k">{'"message_id"'}</span>
          <span className="p">:</span>{" "}
          <span className="s">{'"18f3…c2a"'}</span>
          <span className="p">,</span>
          {"  "}
          <span className="p">{"// PK, dedups replays"}</span>
          {"\n  "}
          <span className="k">{'"primary_label"'}</span>
          <span className="p">:</span>{" "}
          <span className="s">{'"Job Apps"'}</span>
          <span className="p">,</span>{" "}
          <span className="k">{'"flagged"'}</span>
          <span className="p">:</span>{" "}
          <span className="b">true</span>
          <span className="p">,</span>
          {"\n  "}
          <span className="k">{'"model"'}</span>
          <span className="p">:</span>{" "}
          <span className="s">{'"claude-sonnet-4-6"'}</span>
          <span className="p">,</span>
          {"\n  "}
          <span className="k">{'"input_tokens"'}</span>
          <span className="p">:</span>{" "}
          <span className="n">871</span>
          <span className="p">,</span>{" "}
          <span className="k">{'"cache_read_tokens"'}</span>
          <span className="p">:</span>{" "}
          <span className="n">4293</span>
          <span className="p">,</span>{" "}
          <span className="k">{'"output_tokens"'}</span>
          <span className="p">:</span>{" "}
          <span className="n">232</span>
          <span className="p">,</span>
          {"\n  "}
          <span className="k">{'"cost_usd"'}</span>
          <span className="p">:</span>{" "}
          <span className="n">0.0074</span>
          <span className="p">,</span>
          {"\n  "}
          <span className="k">{'"stage_timings_ms"'}</span>
          <span className="p">:</span>
          {" { "}
          <span className="k">{'"fetch"'}</span>
          <span className="p">:</span>{" "}
          <span className="n">191</span>
          <span className="p">,</span>{" "}
          <span className="k">{'"classify"'}</span>
          <span className="p">:</span>{" "}
          <span className="n">5990</span>
          <span className="p">,</span>{" "}
          <span className="p">…</span>
          {" }"}
          {"\n}"}
        </pre>
      </div>
      <div className="panel reveal repo">
        <b>services/</b> triager · job_apps · news_brief · morning_brief · spend_sync
        <br />
        <b>shared/</b> db · gmail · auth · anthropic client · settings
        <br />
        <b>site/</b> public site <span className="c">· reads neon directly, no API layer</span>
        <br />
        <b>tests/</b> {TEST_COUNT} tests <span className="c">· run with zero secrets</span>
        <br />
        <b>migrations/</b> alembic
        <br />
        <span className="x">prompts/ ✂ redacted in the mirror</span>
      </div>
    </div>
  );
}
