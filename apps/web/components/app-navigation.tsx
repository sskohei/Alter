import Link from "next/link";
const links = [
  ["/dashboard", "ホーム"],
  ["/agent", "AI分身"],
  ["/conversations", "交流"],
  ["/notifications", "通知"],
] as const;
export function AppNavigation() {
  return (
    <nav aria-label="メインナビゲーション" className="app-nav">
      {links.map(([href, label]) => (
        <Link key={href} href={href}>
          {label}
        </Link>
      ))}
    </nav>
  );
}
