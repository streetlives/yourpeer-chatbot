// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import "./globals.css";
import { Inter } from "next/font/google";
import type { Metadata, Viewport } from "next";
import { PWARegister } from "./pwa-register";
import { AppleSplashLinks } from "./apple-splash-links";
import { THEME_STORAGE_KEY, DARK_CLASS, TRANSITIONS_OFF_CLASS } from "@/lib/theme";

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
  //
  // interactiveWidget: "resizes-content" tells the browser to shrink
  // the layout viewport when the on-screen keyboard opens, instead
  // of overlaying the keyboard on top of the page. With this set,
  // the chat input + most-recent-message stay visible while typing
  // — without it (the legacy default), iOS Safari hides the input
  // behind the keyboard. Supported in Chrome 108+, Safari 16+;
  // older browsers fall back to the existing dvh-based handling.
  interactiveWidget: "resizes-content",
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
  // Inline FOUC-prevention script. This runs synchronously before first
  // paint, reading the user's stored theme choice + OS preference and
  // applying the .dark class to <html> BEFORE React hydrates. Without
  // this, users in dark mode see a flash of light theme on every load
  // (SSR renders no class → browser paints light → React hydrates →
  // useEffect sets the class → repaint to dark).
  //
  // Why inline + dangerouslySetInnerHTML: external scripts can't run
  // before first paint without a network round-trip. The Tailwind
  // dark: variants only activate when the class is present, so the
  // script must literally run before the body renders.
  //
  // Why duplicate the storage key and "system"/"light"/"dark" string
  // literals from src/lib/theme.ts: this script can't import. The
  // duplication is intentional and the constants are interpolated
  // from the imports above so a rename in theme.ts propagates here at
  // build time.
  //
  // The .theme-transitions-off class suppresses the 120ms color
  // transitions in globals.css for the first paint so users don't see
  // a fade-in on load. We remove it after the first frame via
  // requestAnimationFrame — by that time the initial paint has
  // committed and any subsequent class change (toggle button, OS
  // preference change) gets the smooth animation.
  //
  // Important: the removal runs from the inline script (not from
  // useTheme) so it works on every page, including admin pages and
  // any future routes that don't mount the chat container. Putting
  // this in useTheme would mean the class never lifts on those pages,
  // permanently suppressing the toggle animation if anyone ever adds
  // a theme switcher there.
  const fouc = `(function(){try{var k="${THEME_STORAGE_KEY}";var s=localStorage.getItem(k);var r;if(s==="light"||s==="dark"){r=s}else{r=(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches)?"dark":"light"}var h=document.documentElement;if(r==="dark"){h.classList.add("${DARK_CLASS}")}h.classList.add("${TRANSITIONS_OFF_CLASS}");if(typeof requestAnimationFrame==="function"){requestAnimationFrame(function(){h.classList.remove("${TRANSITIONS_OFF_CLASS}")})}else{setTimeout(function(){h.classList.remove("${TRANSITIONS_OFF_CLASS}")},0)}}catch(e){}})();`;

  return (
    // suppressHydrationWarning: the inline FOUC script may add classes
    // (.dark, .theme-transitions-off) to <html> before React hydrates.
    // SSR has no access to localStorage or matchMedia, so the server
    // renders without those classes. React would otherwise log a
    // hydration mismatch warning. The FOUC script is the canonical
    // pattern for this; suppressing the warning is correct here and
    // ONLY here — body content is not affected.
    //
    // data-scroll-behavior="smooth": opt-in marker for Next.js 15+
    // route-transition handling. Our globals.css sets
    // `html { scroll-behavior: smooth }` so anchor links inside a
    // page animate (the Metrics sticky-nav chips rely on this). But
    // when Next does its scroll-to-top on a NEW route, the same CSS
    // rule animates that jump too — which feels wrong (users expect
    // page changes to land instantly at the top). Adding this
    // attribute tells Next to temporarily disable smooth scroll
    // during its own route-change scroll, then re-enable it, so we
    // keep the in-page smoothness without the cross-page animation.
    // Without this attribute Next 15 emits a console warning at
    // dev time pointing at this exact docs page:
    // https://nextjs.org/docs/messages/missing-data-scroll-behavior
    <html
      lang="en"
      className={inter.className}
      data-scroll-behavior="smooth"
      suppressHydrationWarning
    >
      {/* Next's metadata export writes into <head> automatically; this
          explicit <head> is additive and hosts tags Next can't emit via
          the Metadata API (apple-touch-startup-image with per-device
          media queries, the inline FOUC script). Having both is
          supported. */}
      <head>
        <AppleSplashLinks />
        <script dangerouslySetInnerHTML={{ __html: fouc }} />
      </head>
      <body>
        {children}
        <PWARegister />
      </body>
    </html>
  );
}
