import type { MetadataRoute } from 'next';

// Lets staff add the app to a phone's home screen and open it full screen,
// without the browser's address bar. Android needs HTTPS to offer the install.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Allied Operations',
    short_name: 'Allied',
    description: 'Orders, projects, shipments and Sage for Allied Welding staff.',
    id: '/',
    start_url: '/',
    scope: '/',
    display: 'standalone',
    orientation: 'portrait',
    background_color: '#f8f9fb',
    theme_color: '#f8f9fb',
    icons: [
      { src: '/icon/192', sizes: '192x192', type: 'image/png', purpose: 'any' },
      { src: '/icon/512', sizes: '512x512', type: 'image/png', purpose: 'any' },
      { src: '/icon/512', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
    shortcuts: [
      { name: 'Inbox', url: '/' },
      { name: 'Orders', url: '/orders' },
      { name: 'Projects', url: '/projects' },
      { name: 'Shipments', url: '/shipments' },
    ],
  };
}
