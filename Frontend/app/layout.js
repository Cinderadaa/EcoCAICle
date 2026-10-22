// Note: Files in /public are served from root path (no /public prefix)
export const metadata = {
  title: 'EcoAICle — AI Waste Sorting',
  icons: {
    icon: '/Logo.png',
    apple: '/Logo.png',
  },
  other: {
    google: 'notranslate',
    lang: 'th',
  },
};

import './globals.css';
import GlobalDisclaimer from '@/components/GlobalDisclaimer';

export default function RootLayout({ children }) {
  return (
    <html lang="th">
      <body>
        <main className="tt-main">
          {children}
        </main>
        <GlobalDisclaimer />
      </body>
    </html>
  );
}
