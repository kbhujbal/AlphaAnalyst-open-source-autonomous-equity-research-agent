import type { Metadata } from "next";
import { ReactNode } from "react";

import { Footer } from "@/components/layout/footer";
import { Header } from "@/components/layout/header";

import { Providers } from "./providers";
import "./globals.css";


export const metadata: Metadata = {
  title: "AlphaAnalyst — Equity Research Prototype",
  description:
    "Open-source equity research prototype: given a US stock ticker, " +
    "draft a research memo with experimental valuation and source references.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="flex min-h-screen flex-col font-sans antialiased">
        <Providers>
          <Header />
          <main className="flex-1">{children}</main>
          <Footer />
        </Providers>
      </body>
    </html>
  );
}
