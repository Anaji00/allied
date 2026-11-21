import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="mx-auto grid max-w-sm gap-3 py-16 text-center">
      <h1 className="text-xl font-semibold">That page isn’t here</h1>
      <p className="text-sm text-muted-foreground">It may have moved. Your waiting items are under Today.</p>
      <div className="flex justify-center gap-2">
        <Link href="/inbox" className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground">
          Go to Today
        </Link>
        <Link href="/boards" className="rounded-md border px-3 py-2 text-sm">
          Boards
        </Link>
      </div>
    </div>
  );
}
