import typography from "@tailwindcss/typography";

const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "media",
  theme: {
    extend: {
      colors: {
        bg: token("bg"),
        surface: token("surface"),
        muted: token("muted"),
        border: token("border"),
        fg: token("fg"),
        subtle: token("subtle"),
        accent: token("accent"),
        "accent-2": token("accent-2"),
        ok: token("ok"),
        warn: token("warn"),
        bad: token("bad"),
      },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
    },
  },
  plugins: [typography],
};
