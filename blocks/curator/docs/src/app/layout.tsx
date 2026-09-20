import { RootProvider } from "fumadocs-ui/provider/next";
import { Metadata } from "next";
import "./global.css";

export const metadata: Metadata = {
  title: "LegoFlow Curator",
  description:
    "Convert GitHub PRs into verified SWE-Bench tasks across 8 programming languages.",
};

export default function Layout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="flex flex-col min-h-screen">
        <RootProvider>{children}</RootProvider>
      </body>
    </html>
  );
}
