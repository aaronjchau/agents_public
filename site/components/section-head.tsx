/** Numbered section header: index and title plus a right-aligned mono note. */
export function SectionHead({
  index,
  title,
  note,
}: {
  index: string;
  title: string;
  note?: string;
}) {
  return (
    <div className="sec-head reveal">
      <h2>
        <i>{index}</i>
        {title}
      </h2>
      {note && <span className="note">{note}</span>}
    </div>
  );
}
