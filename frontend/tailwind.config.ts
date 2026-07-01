import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--color-bg-canvas)",
        panel: "var(--color-bg-panel)",
        elevated: "var(--color-bg-elevated)",
        subtle: "var(--color-bg-subtle)",
        active: "var(--color-bg-active)",
        brand: {
          primary: "var(--color-brand-primary)",
          "primary-hover": "var(--color-brand-primary-hover)",
          secondary: "var(--color-brand-secondary)",
          accent: "var(--color-brand-accent)",
        },
        border: {
          subtle: "var(--color-border-subtle)",
          strong: "var(--color-border-strong)",
          focus: "var(--color-border-focus)",
        },
        ink: {
          primary: "var(--color-text-primary)",
          secondary: "var(--color-text-secondary)",
          tertiary: "var(--color-text-tertiary)",
          disabled: "var(--color-text-disabled)",
          inverse: "var(--color-text-inverse)",
        },
        status: {
          success: "var(--color-success)",
          warning: "var(--color-warning)",
          danger: "var(--color-danger)",
          info: "var(--color-info)",
          pending: "var(--color-pending)",
          running: "var(--color-running)",
          degraded: "var(--color-degraded)",
        },
        pipeline: {
          upload: "var(--color-stage-upload)",
          parse: "var(--color-stage-parse)",
          clean: "var(--color-stage-clean)",
          chunk: "var(--color-stage-chunk)",
          quality: "var(--color-stage-quality)",
          embedding: "var(--color-stage-embedding)",
          export: "var(--color-stage-export)",
          retrieval: "var(--color-stage-retrieval)",
          rerank: "var(--color-stage-rerank)",
          generate: "var(--color-stage-generate)",
          score: "var(--color-stage-score)",
        },
        channel: {
          dense: "var(--color-channel-dense)",
          sparse: "var(--color-channel-sparse)",
          structured: "var(--color-channel-structured)",
          rrf: "var(--color-channel-rrf)",
          related: "var(--color-channel-related)",
          context: "var(--color-channel-context)",
        },
      },
      fontFamily: {
        heading: ["var(--font-heading)", "serif"],
        sans: ["var(--font-ui)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      boxShadow: {
        panel: "var(--shadow-md)",
        drawer: "var(--shadow-lg)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
      },
      keyframes: {
        rise: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulseLine: {
          "0%, 100%": { opacity: "0.35" },
          "50%": { opacity: "0.8" },
        },
      },
      animation: {
        rise: "rise 220ms ease-out both",
        "pulse-line": "pulseLine 1.8s ease-in-out infinite",
      },
      backgroundImage: {
        "console-grid":
          "linear-gradient(rgba(16,42,67,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(16,42,67,0.05) 1px, transparent 1px)",
      },
    },
  },
  plugins: [],
};

export default config;
