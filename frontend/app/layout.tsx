import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { ReactNode } from "react";

import { Providers } from "./providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "AlphaAnalyst",
  description:
    "Autonomous equity research agent producing DCF-backed memos with citations.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} font-sans antialiased`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
