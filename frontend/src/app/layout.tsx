import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PlanLint",
  description:
    "Spatio-semantic compliance verification: floor plans bound to building codes",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      {/* suppressHydrationWarning: browser extensions (e.g. Grammarly) inject
          attributes into <body> before hydration; only this element is exempt */}
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
