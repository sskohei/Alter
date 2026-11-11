import Link from "next/link";
import { requireProfile } from "../../lib/api-server";
import { AppNavigation } from "../../components/app-navigation";
import { LogoutButton } from "../../components/logout-button";
import { AlterLogo } from "../../components/landing/alter-logo";
export const dynamic = "force-dynamic";
export default async function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const profile = await requireProfile();
  return (
    <div className="app-shell" key={profile.id}>
      <a href="#content" className="skip-link">
        本文へ移動
      </a>
      <header className="app-header">
        <Link href="/dashboard" className="brand">
          <AlterLogo />
        </Link>
        <span>{profile.display_name}</span>
        <LogoutButton />
      </header>
      <AppNavigation />
      <main id="content" className="app-content">
        {children}
      </main>
    </div>
  );
}
