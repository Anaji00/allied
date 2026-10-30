import './globals.css';
import AppLayout from '@/components/AppLayout';

export const metadata = {
  title: 'Allied Universal Bridge | Cloud to Sage 50 Microservice',
  description: 'Enterprise bridge connecting Monday.com with on-premise Sage 50 US Accounting, 1D nesting math, and dual-sided transaction generation.',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <AppLayout>{children}</AppLayout>
      </body>
    </html>
  );
}
