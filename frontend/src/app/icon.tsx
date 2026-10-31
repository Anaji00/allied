import { appIcon } from '@/lib/app-icon';

// Served at /icon/192 and /icon/512; the manifest points Android at them.
export function generateImageMetadata() {
  return [192, 512].map((px) => ({ id: String(px), size: { width: px, height: px }, contentType: 'image/png' }));
}

export default async function Icon({ id }: { id: Promise<string> }) {
  return appIcon(Number(await id));
}
