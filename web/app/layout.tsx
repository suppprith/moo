import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "moo",
  description: "CS/coding evidence search for AI agents",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
