/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      // Palette: .claude/plugins/root-plugin/resources/DASHBOARD_PALETTE.md.
      // The families keep their Tailwind names — every component already spells
      // them — but resolve to the shared warm tokens defined in src/index.css, so
      // light/dark swap in one place. indigo = brand accent (rust); amber/emerald/
      // teal/rose = the reserved status scale.
      colors: {
        slate: {
          50: "rgb(var(--c-slate-50) / <alpha-value>)",
          100: "rgb(var(--c-slate-100) / <alpha-value>)",
          200: "rgb(var(--c-slate-200) / <alpha-value>)",
          300: "rgb(var(--c-slate-300) / <alpha-value>)",
          400: "rgb(var(--c-slate-400) / <alpha-value>)",
          500: "rgb(var(--c-slate-500) / <alpha-value>)",
          600: "rgb(var(--c-slate-600) / <alpha-value>)",
          700: "rgb(var(--c-slate-700) / <alpha-value>)",
          800: "rgb(var(--c-slate-800) / <alpha-value>)",
          900: "rgb(var(--c-slate-900) / <alpha-value>)",
          950: "rgb(var(--c-slate-950) / <alpha-value>)",
        },
        indigo: {
          200: "rgb(var(--c-accent-200) / <alpha-value>)",
          300: "rgb(var(--c-accent-300) / <alpha-value>)",
          400: "rgb(var(--c-accent-400) / <alpha-value>)",
          500: "rgb(var(--c-accent-500) / <alpha-value>)",
          600: "rgb(var(--c-accent-500) / <alpha-value>)",
        },
        emerald: {
          300: "rgb(var(--c-good) / <alpha-value>)",
          400: "rgb(var(--c-good) / <alpha-value>)",
          500: "rgb(var(--c-good) / <alpha-value>)",
        },
        amber: {
          300: "rgb(var(--c-warn) / <alpha-value>)",
          400: "rgb(var(--c-warn) / <alpha-value>)",
          500: "rgb(var(--c-warn) / <alpha-value>)",
        },
        teal: {
          200: "rgb(var(--c-serious) / <alpha-value>)",
          300: "rgb(var(--c-serious) / <alpha-value>)",
          400: "rgb(var(--c-serious) / <alpha-value>)",
          500: "rgb(var(--c-serious) / <alpha-value>)",
        },
        rose: {
          300: "rgb(var(--c-bad) / <alpha-value>)",
          400: "rgb(var(--c-bad) / <alpha-value>)",
          500: "rgb(var(--c-bad) / <alpha-value>)",
        },
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
