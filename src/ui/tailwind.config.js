/** Design tokens — NEUROX black + red theme (CLAUDE.md §11) */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        shell: '#050506',
        ink: '#ECECEE',
        dim: '#6B6B76',
        dimmer: '#48484F',
        soft: '#9C9CA4',
        softer: '#8B8B94',
        body: '#C7C7CE',
        red1: '#DC2626',
        red2: '#EF4444',
        red3: '#FCA5A5',
        mint: '#34D399',
        rose: '#FB7185',
        amber: '#FBBF24',
        amberlight: '#FDE68A',
      },
      fontFamily: {
        sans: ['Geist', 'system-ui', 'sans-serif'],
        mono: ['"Geist Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
}
