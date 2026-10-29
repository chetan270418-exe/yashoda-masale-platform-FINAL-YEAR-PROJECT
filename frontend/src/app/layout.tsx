import type { Metadata } from "next";
import React from "react";

export const metadata: Metadata = {
  title: "Yashoda Masale | Authentic Spices & Traceable Heritage",
  description: "Web-first spice manufacturing, distribution, and batch traceability platform.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "system-ui, -apple-system, sans-serif" }}>
        {children}
      </body>
    </html>
  );
}
