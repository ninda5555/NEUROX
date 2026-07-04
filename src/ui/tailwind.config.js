/** Design tokens from design/project/NSE Trading Assistant.dc.html */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        shell: '#0A0E17',
        ink: '#E6EAF2',
        dim: '#6B7488',
        dimmer: '#4A5468',
        soft: '#9AA3B5',
        softer: '#8A93A6',
        body: '#C3CAD8',
        violet1: '#8B5CF6',
        violet2: '#A78BFA',
        violet3: '#C4B5FD',
        mint: '#34D399',
        rose: '#FB7185',
        peri: '#818CF8',
        perilight: '#C7D2FE',
      },
      fontFamily: {
        sans: ['Geist', 'system-ui', 'sans-serif'],
        mono: ['"Geist Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
}
