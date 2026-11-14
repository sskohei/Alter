// Logo 03 "glyph swap": the "e" of alter becomes two overlapping circles —
// a filled one (you) and an outlined one (your AI alter ego).
// `outline` draws the same logo as hairlines only (letters via text-stroke, the ring as its two edges).
export function AlterMark({ className, outline = false }: { className?: string; outline?: boolean }) {
  if (outline) {
    return (
      <svg className={className} viewBox="0 0 100 70" aria-hidden="true" focusable="false">
        <circle className="you" cx="41" cy="35" r="30" />
        <circle className="edge" cx="61" cy="35" r="32" />
        <circle className="edge" cx="61" cy="35" r="26" />
      </svg>
    );
  }
  return (
    <svg className={className} viewBox="0 0 100 70" aria-hidden="true" focusable="false">
      <circle cx="41" cy="35" r="30" fill="currentColor" />
      <circle cx="61" cy="35" r="29" fill="none" stroke="currentColor" strokeWidth="6" />
    </svg>
  );
}

export function AlterLogo({ className = "", decorative = false, outline = false }: { className?: string; decorative?: boolean; outline?: boolean }) {
  const a11y = decorative ? { "aria-hidden": true } : { role: "img", "aria-label": "Alter" };
  return (
    <span className={`lp-logo${outline ? " is-outline" : ""} ${className}`} {...a11y}>
      <span aria-hidden="true">alt</span>
      <AlterMark outline={outline} />
      <span aria-hidden="true">r</span>
    </span>
  );
}
