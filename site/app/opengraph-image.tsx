import { ImageResponse } from "next/og";

import type { CSSProperties } from "react";

import { COST_PER_EMAIL_USD, TEST_COUNT } from "@/lib/site";

const COST_LABEL = `$${COST_PER_EMAIL_USD.toFixed(2)}`;

// tokens hardcoded from globals.css :root; ImageResponse can't read CSS vars
const BG = "#0b0b0d";
const TEXT = "#edece7";
const DIM = "#9a988e";
const AMBER = "#ffb454";

const STATS: Array<[string, string]> = [
  ["5", "agents"],
  ["12", "labels"],
  [String(TEST_COUNT), "tests"],
  [COST_LABEL, "per email"],
];

// corner ticks for the schematic feel (matches .figure .corner)
const TICK = "2px solid " + AMBER;
const TICKS: CSSProperties[] = [
  { top: 32, left: 32, borderTop: TICK, borderLeft: TICK },
  { top: 32, right: 32, borderTop: TICK, borderRight: TICK },
  { bottom: 32, left: 32, borderBottom: TICK, borderLeft: TICK },
  { bottom: 32, right: 32, borderBottom: TICK, borderRight: TICK },
];

export const alt = `agents: a personal agent fleet. 5 agents · 12 labels · ${TEST_COUNT} tests · ${COST_LABEL} per email.`;

export const size = {
  width: 1200,
  height: 630,
};

export const contentType = "image/png";

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          position: "relative",
          padding: "64px 88px",
          background: BG,
          color: TEXT,
          fontFamily: "sans-serif",
        }}
      >
        {TICKS.map((pos, i) => (
          <div
            key={i}
            style={{ position: "absolute", width: 22, height: 22, opacity: 0.8, ...pos }}
          />
        ))}

        <div style={{ display: "flex", fontSize: 38, fontWeight: 800, letterSpacing: "-0.02em" }}>
          <span>agents</span>
          <span style={{ color: AMBER }}>*</span>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            flexGrow: 1,
          }}
        >
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              fontSize: 100,
              fontWeight: 700,
              letterSpacing: "-0.03em",
              lineHeight: 1.06,
            }}
          >
            <span>a personal</span>
            <span style={{ display: "flex" }}>
              {"agent\u00A0"}
              <span style={{ color: AMBER }}>fleet</span>
            </span>
          </div>

          <div style={{ width: 96, height: 2, background: AMBER, opacity: 0.9, marginTop: 40 }} />

          <div
            style={{
              display: "flex",
              alignItems: "center",
              marginTop: 32,
              fontSize: 27,
              letterSpacing: "0.08em",
              color: DIM,
            }}
          >
            {STATS.map(([n, label], i) => (
              <span key={label} style={{ display: "flex" }}>
                {i > 0 && <span style={{ color: AMBER, margin: "0 18px" }}>·</span>}
                <span style={{ color: TEXT }}>{n}</span>
                <span>{"\u00A0" + label}</span>
              </span>
            ))}
          </div>
        </div>
      </div>
    ),
    {
      ...size,
    },
  );
}
