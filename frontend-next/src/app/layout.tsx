// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import "./globals.css";
import { Inter } from "next/font/google";
import type { Metadata, Viewport } from "next";
import { PWARegister } from "./pwa-register";

const inter = Inter({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
});

export const viewport: Viewport = {
  themeColor: "#FFD54F",
  // Cover iOS safe areas so standalone mode doesn't look cut off
  viewportFit: "cover",
  // Mobile users often land here in a moment of stress — don't let
  // them pinch-zoom accidentally while tapping buttons. maximumScale
  // is a balance between a11y (users who need to zoom) and correct
  // layout on small screens. Stick with defaults (zooming allowed).
};

export const metadata: Metadata = {
  title: "YourPeer — Find Services",
  description:
    "Find shelter, food, and other services in NYC. Your conversation is private.",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "YourPeer",
  },
  icons: {
    icon: "/icons/icon-192.png",
    apple: "/icons/icon-192.png",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.className}>
      <body>
        {children}
        <PWARegister />
      </body>
    </html>
  );
}
