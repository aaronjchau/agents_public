import type { Metadata } from "next";

import { Cta } from "@/components/cta";
import { PipelinePanels } from "@/components/email/pipeline-panels";
import { SorterFig } from "@/components/email/sorter-fig";
import { StatusLineFig } from "@/components/email/status-line-fig";
import { SectionHead } from "@/components/section-head";

export const metadata: Metadata = {
  title: "email",
  description:
    "How one prompt-cached model call sorts every inbound email into 12 labels and feeds job-application email into the Notion pipeline.",
};

export default function EmailPage() {
  return (
    <main className="page-anim">
      <div className="wrap">
        <section className="hero solo">
          <div>
            <h1 className="reveal-now">
              Every email gets <em>one label</em>.
            </h1>
            <p className="reveal-now">
              One model call sorts each email into one of 12 labels. Job-related email continues
              into the Job Apps pipeline.
            </p>
            <div className="statline reveal-now">
              <span>
                <b>12</b> labels · <b>1</b> per email
              </span>
              <span>
                <b>7</b> job-app sublabels
              </span>
              <span>
                <b>1</b> LLM call per email
              </span>
              <span>
                <b>0</b> replies sent by machine
              </span>
            </div>
          </div>
        </section>

        <section className="sec" style={{ paddingTop: 40 }}>
          <SectionHead index="01" title="The sorter" />
          <SorterFig />
        </section>

        <section className="sec">
          <SectionHead
            index="02"
            title="Job Apps pipeline"
            note="langgraph · opus · fired by dispatch"
          />
          <PipelinePanels />
        </section>

        <section className="sec">
          <SectionHead
            index="03"
            title="The one-way line"
            note="status is monotonic · terminal stations are read-only"
          />
          <StatusLineFig />
        </section>

        <Cta
          title={
            <>
              Nothing here <em>sends email</em>.
            </>
          }
          blurb="The pipeline labels and matches. Every reply is written by hand."
          nextHref="/briefs"
          nextLabel="briefs →"
        />
      </div>
    </main>
  );
}
