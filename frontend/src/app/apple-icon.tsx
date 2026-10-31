import { appIcon } from '@/lib/app-icon';

// iPhones ignore the manifest's icons and ask for this one when added to the home screen.
export const size = { width: 180, height: 180 };
export const contentType = 'image/png';

export default function AppleIcon() {
  return appIcon(180);
}
