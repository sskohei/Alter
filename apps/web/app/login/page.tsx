import Link from "next/link";
import { login } from "./actions";
import { AlterLogo } from "../../components/landing/alter-logo";
import { accessMessage, safeReturnPath } from "../../lib/auth-policy";
export default async function Login({ searchParams }: { searchParams: Promise<{ error?: string; next?: string }> }) {
  const params = await searchParams;
  return <main className="public-page"><Link href="/" className="brand"><AlterLogo /></Link><h1>あなたのAI分身をはじめる</h1>
    <p>Alterは18歳以上の招待者向けの試験サービスです。AIの発言は生成された内容であり、本人の発言・意思を表すものではありません。誤った内容を生成する場合があります。</p>
    {params.error && <div role="alert" className="notice">{accessMessage(params.error)}</div>}
    <p><Link href="/deletion-status">削除要求済みの方は処理状況を確認</Link></p>
    <p><Link href="/legal">利用規約案・プライバシー説明を読む</Link>（正式文面は公開前に確認します）</p>
    <form action={login} className="stack"><input type="hidden" name="next" value={safeReturnPath(params.next)} />
      <label className="check"><input type="checkbox" name="consent" value="yes" required />18歳以上で、利用規約案に同意し、プライバシー説明を確認しました。</label>
      <button name="provider" value="google">Googleでログイン</button><button name="provider" value="github" className="secondary">GitHubでログイン</button>
    </form><p>ログインだけで自律交流やDiscord公開が始まることはありません。</p>
  </main>;
}
