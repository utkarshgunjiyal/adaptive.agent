/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./src/**/*.{js,jsx,ts,tsx}', './public/index.html'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['IBM Plex Sans', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        serif: ['Cormorant Garamond', 'Georgia', 'serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      colors: {
        ink: {
          bg: '#FDFBF7',
          surface: '#FFFFFF',
          surfaceAlt: '#F3F1EC',
          text: '#111111',
          textMuted: '#525252',
          border: '#E5E2DC',
        },
        night: {
          bg: '#0B0B0C',
          surface: '#151517',
          surfaceAlt: '#1D1D20',
          text: '#F5F2EC',
          textMuted: '#A1A1A6',
          border: '#26262A',
        },
        signal: {
          doc: '#059669', // private doc — emerald
          paper: '#4F46E5', // research paper — indigo
          web: '#E11D48', // web source — rose
          ctx: '#64748B', // conversation context — slate
        },
      },
      boxShadow: {
        card: '0 1px 0 0 rgba(0,0,0,0.02), 0 0 0 1px rgba(0,0,0,0.06)',
        cardDark: '0 1px 0 0 rgba(255,255,255,0.04), 0 0 0 1px rgba(255,255,255,0.06)',
      },
      borderRadius: {
        sm: '2px',
        DEFAULT: '4px',
      },
      keyframes: {
        blink: { '0%,100%': { opacity: '1' }, '50%': { opacity: '0' } },
        pulseDot: { '0%,100%': { opacity: '0.4' }, '50%': { opacity: '1' } },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        blink: 'blink 1s step-end infinite',
        pulseDot: 'pulseDot 1.4s ease-in-out infinite',
        slideUp: 'slideUp 240ms cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [],
};
