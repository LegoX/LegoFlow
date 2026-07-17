import Link from "next/link";

// Static-export-friendly landing: link into the docs. The production redirect
// (/ -> /docs) is handled by Cloudflare Pages via public/_redirects.
export default function HomePage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <h1 className="text-2xl font-semibold">Terminal-gen</h1>
      <p className="text-fd-muted-foreground max-w-md">
        Convert StackOverflow Q&amp;A into verified terminal-bench tasks across
        13 task domains.
      </p>
      <Link
        href="/docs"
        className="rounded-md bg-fd-primary px-4 py-2 text-fd-primary-foreground"
      >
        Read the docs
      </Link>
    </main>
  );
}
