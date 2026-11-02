import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Alter | 開発環境", description: "Alter 開発用の起動確認" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ja"><body>{children}</body></html>;
}
