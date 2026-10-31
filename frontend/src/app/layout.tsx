import type { Metadata, Viewport } from 'next';
import type { ReactNode } from 'react';
import { IBM_Plex_Mono, IBM_Plex_Sans } from 'next/font/google';
import { Providers } from '@/components/providers';
import { AppShell } from '@/components/shell/app-shell';
import './globals.css';

// Plex has clear figures and a mono that suits SO numbers, heats and part codes.
const sans = IBM_Plex_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-plex-sans',
  display: 'swap',
});
const mono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-plex-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: { default: 'Allied Operations', template: '%s · Allied Operations' },
  description: 'Orders, projects, shipments and Sage for Allied Welding staff.',
  // Opened from an iPhone home screen: full screen, named "Allied" under the icon.
  appleWebApp: { capable: true, title: 'Allied', statusBarStyle: 'default' },
};

export const viewport: Viewport = {
  // "cover" lets the phone tab bar pad itself clear of the iPhone home bar.
  viewportFit: 'cover',
  // The phone's status bar matches the page background in each theme.
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#f8f9fb' },
    { media: '(prefers-color-scheme: dark)', color: '#111317' },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // next-themes sets the theme class before React hydrates.
    <html lang="en" suppressHydrationWarning className={`${sans.variable} ${mono.variable}`}>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
