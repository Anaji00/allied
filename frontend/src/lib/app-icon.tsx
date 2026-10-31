import { ImageResponse } from 'next/og';

// The Allied diamond on the logo's dark ground, for home-screen icons. The
// diamond spans 60% of the square, inside the circle Android keeps when it
// crops a "maskable" icon, so one drawing serves every platform.
const RED = '#D32F2F';
const INK = '#1A1A2E';

const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">
<polygon points="20,0 40,20 20,40 0,20" fill="${RED}"/>
<polygon points="20,8 32,20 20,32 8,20" fill="${INK}"/>
<path d="M20,12 L26,20 L20,28 L14,20 Z" fill="${RED}"/>
</svg>`;

export function appIcon(size: number) {
  const mark = Math.round(size * 0.6);
  return new ImageResponse(
    (
      <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: INK }}>
        {/* eslint-disable-next-line @next/next/no-img-element -- rendered to PNG, not shown in the page */}
        <img alt="" width={mark} height={mark} src={`data:image/svg+xml,${encodeURIComponent(svg)}`} />
      </div>
    ),
    { width: size, height: size },
  );
}
