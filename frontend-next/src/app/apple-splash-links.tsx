// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * iOS PWA splash-screen <link> tags.
 *
 * iOS doesn't derive launch images from the Web App Manifest. Without
 * explicit `apple-touch-startup-image` links, launching an installed
 * PWA on iPhone shows a white screen for 1-3 seconds — feels broken.
 *
 * Each image must be matched by a device-specific media query. The
 * query has four axes:
 *   - device-width / device-height in CSS pixels (not physical)
 *   - -webkit-device-pixel-ratio (DPR)
 *   - orientation (we only emit portrait; manifest is portrait-locked)
 *
 * Sizes verified against Apple's device specs as of 2026. Not all
 * historical devices are covered — devices older than iPhone SE 2nd gen
 * (2020) and iPads older than 2018 are omitted. Users on those see the
 * default white splash; they're <1% of traffic for this app.
 *
 * To add more devices: append to SPLASH_LINKS with the physical image
 * size, CSS dimensions, DPR, and the image file (which must already
 * exist in /public/splash/).
 */

type SplashLink = {
  /** Physical image dimensions (CSS pixels × DPR). Filename matches. */
  width: number;
  height: number;
  /** CSS pixel dimensions used in the media query. */
  cssWidth: number;
  cssHeight: number;
  /** Device pixel ratio. */
  dpr: number;
  /** Human-readable note — appears in source only, helps when debugging. */
  device: string;
};

const SPLASH_LINKS: readonly SplashLink[] = [
  // --- iPhone portrait ---
  { width: 1290, height: 2796, cssWidth: 430, cssHeight: 932, dpr: 3, device: "iPhone 14/15 Pro Max" },
  { width: 1284, height: 2778, cssWidth: 428, cssHeight: 926, dpr: 3, device: "iPhone 14/15 Plus, 12/13 Pro Max" },
  { width: 1179, height: 2556, cssWidth: 393, cssHeight: 852, dpr: 3, device: "iPhone 14/15 Pro" },
  { width: 1170, height: 2532, cssWidth: 390, cssHeight: 844, dpr: 3, device: "iPhone 12/13/14/15 (non-Pro)" },
  { width: 1125, height: 2436, cssWidth: 375, cssHeight: 812, dpr: 3, device: "iPhone X/XS/11 Pro/12 mini/13 mini" },
  { width: 828, height: 1792, cssWidth: 414, cssHeight: 896, dpr: 2, device: "iPhone XR/11" },
  { width: 750, height: 1334, cssWidth: 375, cssHeight: 667, dpr: 2, device: "iPhone SE 2nd/3rd gen, 6/7/8" },

  // --- iPad portrait ---
  { width: 2048, height: 2732, cssWidth: 1024, cssHeight: 1366, dpr: 2, device: "iPad Pro 12.9\"" },
  { width: 1668, height: 2388, cssWidth: 834, cssHeight: 1194, dpr: 2, device: "iPad Pro 11\"" },
  { width: 1640, height: 2360, cssWidth: 820, cssHeight: 1180, dpr: 2, device: "iPad Air 5th gen" },
  { width: 1620, height: 2160, cssWidth: 810, cssHeight: 1080, dpr: 2, device: "iPad 10th gen" },
  { width: 1488, height: 2266, cssWidth: 744, cssHeight: 1133, dpr: 2, device: "iPad mini 6" },
] as const;

export function AppleSplashLinks() {
  return (
    <>
      {SPLASH_LINKS.map((s) => (
        <link
          key={`${s.width}x${s.height}`}
          rel="apple-touch-startup-image"
          href={`/splash/apple-splash-${s.width}x${s.height}.png`}
          media={`(device-width: ${s.cssWidth}px) and (device-height: ${s.cssHeight}px) and (-webkit-device-pixel-ratio: ${s.dpr}) and (orientation: portrait)`}
        />
      ))}
    </>
  );
}
