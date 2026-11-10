/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: "#0B1F3A", 50: "#F1F4F9", 100: "#E2E8F2", 700: "#16305A", 800: "#0F2747", 900: "#0B1F3A" },
        teal: { DEFAULT: "#14B8A6", 50: "#ECFDFA", 100: "#CCFBF1", 600: "#0D9488", 700: "#0F766E" },
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"] },
    },
  },
  plugins: [],
};
