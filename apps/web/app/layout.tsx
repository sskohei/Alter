import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Alter | あなたのAI分身", description: "あなたが承認したAI分身と、新しいつながりへ。" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ja"><body>{children}</body></html>;
}
