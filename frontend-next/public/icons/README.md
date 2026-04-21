# YourPeer PWA Icons

The PWA manifest references three icon files in this directory. Until you
provide real YourPeer brand assets, the site will work in the browser but
"Add to Home Screen" will show a broken-icon placeholder on mobile devices.

## Required files

| Filename | Size | Purpose |
|---|---|---|
| `icon-192.png` | 192×192 | Standard home-screen icon on most platforms |
| `icon-512.png` | 512×512 | High-res home-screen icon, splash screen |
| `icon-maskable-512.png` | 512×512 | Android adaptive icon (see note below) |

All three must be PNG. Transparency is fine for the first two; the
maskable one should NOT use transparency — see below.

## About the maskable icon

Android's adaptive-icon system crops the icon into a shape (circle,
rounded square, etc.) chosen by the device. The "maskable" icon must
have its meaningful content inside a **centered 409×409 safe zone**
(80% of the 512×512 canvas) with the outer ring designed to be cropped
cleanly. No transparency on the outer ring — use the brand background
color (`#FFD54F` per `manifest.webmanifest`) or a solid color chosen
by design.

Tool: [https://maskable.app/editor](https://maskable.app/editor) lets
you upload a square icon and verify how it crops in different shapes.

## Brand color

The manifest declares `theme_color: #FFD54F` (yellow). This is the
color browsers use for the splash screen and title bar on Android.
If you change the brand primary color, update:

1. `public/manifest.webmanifest` — `theme_color` and `background_color`
2. `src/app/layout.tsx` — the `themeColor` in the `viewport` export

## Apple-specific note

iOS ignores the web manifest and uses `<link rel="apple-touch-icon">`
in the HTML head instead. We reference `/icons/icon-192.png` for that
too, so if you only ship the three files above, Apple home-screen
icons will also work (though not in true "maskable" form — iOS doesn't
support maskable icons).

## Testing your icons

Once placed in this directory:

1. Run the dev server (`npm run dev`)
2. Open Chrome DevTools → Application → Manifest — verify icons load
3. On Android Chrome, tap the install prompt; the installed app should
   use your icons
4. On desktop Chrome, the address-bar install button (⊕) should show
   your icon in the install dialog
