import type { Config } from "tailwindcss";
/* eslint-disable @typescript-eslint/no-require-imports */

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Helvetica Neue", "Helvetica", "sans-serif"],
      },
      colors: {
        amber: {
          300: "#FFD54F",
        },
        primary: "#FFDC00",
        dark: "#323232",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [
    require("@tailwindcss/forms"),
    require("tailwind-scrollbar-hide"),
    require("tailwindcss-animate"),
  ],
};
export default config;
