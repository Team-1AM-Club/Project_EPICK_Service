import type { Metadata } from "next";
import "./globals.css";
import "./figma-theme.css";
import { AppProviders } from "./providers";

export const metadata: Metadata = {
  title: "EPICK — 나의 경험, 다음 기회",
  description: "경험을 기록하고, 기업과 문항에 맞는 나만의 자기소개서 소재를 발견하세요.",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: [
      {
        url: "/epick-favicon.png",
        type: "image/png",
        sizes: "110x110",
      },
    ],
    shortcut: "/epick-favicon.png",
    apple: "/epick-favicon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="antialiased"><AppProviders>{children}</AppProviders></body>
    </html>
  );
}
