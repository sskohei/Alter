import Link from "next/link";
export default function Onboarding() {
  return <><h1>AI分身をつくる準備</h1><p>公開してよい性格・価値観・興味を整理しましょう。住所、連絡先、機微情報や第三者の秘密は入力しないでください。</p><Link className="button" href="/agent/new">手動で人格を入力する</Link></>;
}
