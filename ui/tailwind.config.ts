import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)", "sans-serif"],
      },
      colors: {
        // lifecycle 상태 색 — UI 전반에서 일관되게
        active: "#22c55e",    // 🟢 green-500
        evolving: "#eab308",  // 🟡 yellow-500
        resolved: "#71717a",  // ⚫ zinc-500
      },
    },
  },
  plugins: [],
};

export default config;
