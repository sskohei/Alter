import Image from "next/image";
import pair from "../public/visuals/pair.png";

// Decorative live preview of the agent being written. The form is the source of truth,
// so the whole card is hidden from assistive tech.
export function AlterCardPreview({
  name,
  tags,
  status,
  version = "v0",
}: {
  name: string;
  tags: string[];
  status: string;
  version?: string;
}) {
  return (
    <aside className="ob-side" aria-hidden="true">
      <div className="alter-card">
        <span className="draft">DRAFT</span>
        <p className="k">
          <span>ALTER CARD</span>
          <span>{version}</span>
        </p>
        <div className="stage">
          <div className="float" key={name ? "named" : "empty"}>
            <Image src={pair} alt="" className={`float-obj${name ? " jelly-once" : ""}`} sizes="240px" />
          </div>
        </div>
        <p className={`card-name${name ? "" : " empty"}`}>{name || "名前はまだ"}</p>
        <p className="owner">{status}</p>
        <div className="stickers">
          {tags.map((t, i) => (
            <span key={`${t}-${i}`} className="sticker">
              {t}
            </span>
          ))}
        </div>
        <p className="note">入力するとカードができあがっていきます。</p>
      </div>
    </aside>
  );
}

export const cardTags = (...texts: (string | undefined)[]) =>
  texts
    .join("、")
    .split(/[、,，\n]/)
    .map((t) => t.trim())
    .filter(Boolean)
    .slice(0, 8);
