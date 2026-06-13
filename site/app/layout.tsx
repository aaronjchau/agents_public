import type { Metadata, Viewport } from "next";
import { DM_Mono, Hanken_Grotesk } from "next/font/google";

import { ScrollFx } from "@/components/scroll-fx";
import { SiteNav } from "@/components/site-nav";
import { SITE_URL } from "@/lib/site";
import "./globals.css";

const sans = Hanken_Grotesk({
  subsets: ["latin"],
  variable: "--font-hanken",
});

const mono = DM_Mono({
  weight: ["400", "500"],
  subsets: ["latin"],
  variable: "--font-dm-mono",
});

const TITLE = "agents - personal fleet";
const DESCRIPTION =
  "Five serverless agents that triage email, advance a job pipeline in Notion, write two daily briefs, and reconcile their own API bill. Every run leaves an audit row in Postgres.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: TITLE,
    template: "%s · agents",
  },
  description: DESCRIPTION,
  openGraph: {
    siteName: "agents",
    type: "website",
    url: "/",
    title: TITLE,
    description: DESCRIPTION,
  },
  twitter: {
    card: "summary_large_image",
  },
};

export const viewport: Viewport = {
  themeColor: "#0b0b0d",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} antialiased`} suppressHydrationWarning>
      <head>
        {/* runs before first paint: hidden reveal states in globals.css only apply under html.js */}
        <script
          dangerouslySetInnerHTML={{ __html: "document.documentElement.classList.add('js')" }}
        />
      </head>
      <body>
        <div className="glow" aria-hidden="true" />
        <SiteNav />
        {children}
        <footer className="site-footer">
          <div className="wrap">
            <span>
              <em>*</em>This is a public mirror of my personal production system
            </span>
          </div>
        </footer>
        <ScrollFx />
      </body>
    </html>
  );
}
