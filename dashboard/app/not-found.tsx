import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-content p-6">
      <h1 className="font-display text-[28px] font-semibold text-text">Page not found</h1>
      <p className="mt-2 text-text-2">That page does not exist.</p>
      <Link href="/" className="mt-4 inline-block text-primary underline">
        Back to Overview
      </Link>
    </main>
  );
}
