import Link from "next/link";
import { requireProfile } from "../../lib/api-server";
import { logout } from "../login/actions";
export const dynamic = "force-dynamic";
export default async function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const profile = await requireProfile();
  return <div className="app-shell"><a href="#content" className="skip-link">本文へ移動</a>
    <header className="app-header"><Link href="/dashboard" className="brand">Alter</Link><span>{profile.display_name}</span><form action={logout}><button className="secondary">ログアウト</button></form></header>
    <nav aria-label="メインナビゲーション" className="app-nav"><Link href="/dashboard">ホーム</Link><Link href="/agent">AI分身</Link><Link href="/onboarding">作成ガイド</Link></nav>
    <main id="content" className="app-content">{children}</main></div>;
}
