/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: "#0B1F3A", 50: "#F1F4F9", 100: "#E2E8F2", 700: "#16305A", 800: "#0F2747", 900: "#0B1F3A" },
        // Care palette: navy base, sky blue for light surfaces, deep blue for buttons and text.
        sky: { DEFAULT: "#1E6FB8", 50: "#EEF7FD", 100: "#D6ECF8", 300: "#5BB4E5", 600: "#195E9C", 700: "#17588F", 800: "#12446E" },
        // Homeowner app only: warm peach for the friendliest actions, warm off-white page.
        peach: { DEFAULT: "#F4A58A", 100: "#FCE6DD", 600: "#E8896B", 900: "#4A2416" },
        cream: "#FBF8F4",
      },
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"] },
    },
  },
  plugins: [],
};
