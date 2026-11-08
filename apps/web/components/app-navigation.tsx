"use client";
import Link from "next/link";
const links = [
  ["/dashboard", "ホーム"],
  ["/agent", "AI分身"],
  ["/conversations", "交流"],
  ["/notifications", "通知"],
  ["/settings/interaction", "交流設定"],
  ["/memories", "記憶"],
  ["/relationships", "交流相手"],
  ["/settings/account", "アカウント"],
] as const;
export function AppNavigation() {
  const primary = links.filter(([href]) =>
    ["/dashboard", "/conversations", "/notifications"].includes(href),
  );
  const more = links.filter(
    ([href]) =>
      !["/dashboard", "/conversations", "/notifications"].includes(href),
  );
  return (
    <>
      <nav aria-label="メインナビゲーション" className="app-nav desktop-nav">
        {links.map(([href, label]) => (
          <Link key={href} href={href}>
            {label}
          </Link>
        ))}
      </nav>
      <nav aria-label="メインナビゲーション" className="app-nav mobile-nav">
        {primary.map(([href, label]) => (
          <Link key={href} href={href}>
            {label}
          </Link>
        ))}
        <details className="nav-more">
          <summary>その他</summary>
          <div className="nav-menu">
            {more.map(([href, label]) => (
              <Link
                key={href}
                href={href}
                onClick={(event) =>
                  event.currentTarget
                    .closest("details")
                    ?.removeAttribute("open")
                }
              >
                {label}
              </Link>
            ))}
          </div>
        </details>
      </nav>
    </>
  );
}
