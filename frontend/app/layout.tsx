import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { ReactNode } from "react";

import { Footer } from "@/components/layout/footer";
import { Header } from "@/components/layout/header";

import { Providers } from "./providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "AlphaAnalyst — Autonomous Equity Research",
  description:
    "Open-source autonomous equity research agent: given a US stock ticker, " +
    "produce a research memo with DCF valuation, news analysis, and full citations.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} flex min-h-screen flex-col font-sans antialiased`}>
        <Providers>
          <Header />
          <main className="flex-1">{children}</main>
          <Footer />
        </Providers>
      </body>
    </html>
  );
}
