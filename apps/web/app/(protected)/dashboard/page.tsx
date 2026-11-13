import Link from "next/link";
export default function Dashboard() {
  return <><p className="eyebrow">YOUR AI COMPANION</p><h1>おかえりなさい</h1><section className="card"><h2>あなたのAI分身</h2><p>人格を確認・承認してから、交流設定を選びます。自律交流とDiscord公開は初期OFFです。</p><Link className="button" href="/agent">AI分身を確認する</Link></section></>;
}
