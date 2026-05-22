import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Match the design tokens exactly — these are also exported as CSS vars
        // in globals.css for use in Recharts and inline styles.
        bg: {
          DEFAULT: '#0a0e0a',
          alt: '#0f1410',
        },
        ink: {
          DEFAULT: '#e8f0d8',
          dim: '#8a9686',
        },
        green: '#6dd366',
        amber: '#f4b942',
        red: '#ff5b5b',
        blue: '#5cc8ff',
        magenta: '#ff6bd6',
        line: '#1c2418',
      },
      fontFamily: {
        sans: ['var(--font-space-grotesk)', 'system-ui', 'sans-serif'],
        display: ['var(--font-fraunces)', 'Georgia', 'serif'],
        mono: ['var(--font-jetbrains-mono)', 'ui-monospace', 'monospace'],
      },
      letterSpacing: {
        label: '0.14em',
      },
      borderRadius: {
        // Cap radii: nothing rounder than 4px per the spec
        none: '0',
        sm: '2px',
        DEFAULT: '4px',
      },
      keyframes: {
        'pulse-dot': {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.45', transform: 'scale(0.85)' },
        },
      },
      animation: {
        'pulse-dot': 'pulse-dot 1.8s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};

export default config;
