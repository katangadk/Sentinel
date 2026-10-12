import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: 'Sentinel · Laptop security', description: 'Private laptop status, approximate location and remote screen locking.',
  manifest: '/manifest.webmanifest', appleWebApp: { capable: true, title: 'Sentinel', statusBarStyle: 'default' },
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
