import Link from "next/link";

/** Closing band: heading + blurb plus the next-page link (the repo link lives in the nav). */
export function Cta({
  title,
  blurb,
  nextHref,
  nextLabel,
}: {
  title: React.ReactNode;
  blurb: string;
  nextHref: string;
  nextLabel: string;
}) {
  return (
    <div className="cta reveal">
      <div>
        <h3>{title}</h3>
        <p>{blurb}</p>
      </div>
      <div>
        <Link className="btn" href={nextHref}>
          {nextLabel}
        </Link>
      </div>
    </div>
  );
}
