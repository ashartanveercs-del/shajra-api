import type { Metadata } from "next";
import "./globals.css";
import Navbar from "@/components/Navbar";

export const metadata: Metadata = {
  title: "Shajra — Family Heritage",
  description:
    "Our private family heritage platform. Explore our genealogy through an interactive tree, detailed member profiles, and historical maps.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&family=Inter:wght@300;400;500;600;700&display=swap"
          rel="stylesheet"
        />
        <link
          rel="stylesheet"
          href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
          integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
          crossOrigin=""
        />
      </head>
      <body className="min-h-full flex flex-col bg-bg-primary text-text-primary antialiased">
        <Navbar />
        <main className="flex-1 shrink-0">{children}</main>
        <footer className="w-full py-8 mt-12 border-t border-border bg-bg-secondary/50 text-center text-sm text-text-muted tracking-wide animate-fadeIn">
          Made and maintained by <span className="font-semibold text-accent">Ashar Tanveer</span><br/>
          <span className="text-xs opacity-70 mt-1.5 inline-block">WhatsApp: 03369381947 &mdash; For queries, edits, or suggestions</span>
        </footer>
      </body>
    </html>
  );
}
