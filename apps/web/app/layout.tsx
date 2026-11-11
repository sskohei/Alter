import type { Metadata } from "next";
import { M_PLUS_1, M_PLUS_1_Code, Space_Mono, Unbounded, Zen_Kaku_Gothic_New } from "next/font/google";
import "./globals.css";

// Brand fonts are exposed only as CSS variables; screens opt in explicitly.
const display = Unbounded({ subsets: ["latin"], weight: ["400", "700", "900"], variable: "--font-display", display: "swap" });
const mono = Space_Mono({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-mono", display: "swap" });
const jp = Zen_Kaku_Gothic_New({ subsets: ["latin"], weight: ["300", "400", "500", "700"], variable: "--font-jp", display: "swap", preload: false });
// Variable families that go down to weight 100, for the ultra-thin body/label text.
const jpThin = M_PLUS_1({ subsets: ["latin"], weight: "variable", variable: "--font-jp-thin", display: "swap", preload: false });
const monoThin = M_PLUS_1_Code({ subsets: ["latin"], weight: "variable", variable: "--font-mono-thin", display: "swap", preload: false });

export const metadata: Metadata = { title: "Alter | あなたのAI分身", description: "あなたが承認したAI分身と、新しいつながりへ。" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ja" className={`${display.variable} ${mono.variable} ${jp.variable} ${jpThin.variable} ${monoThin.variable}`}><body>{children}</body></html>;
}
