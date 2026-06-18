import { RootProvider } from "fumadocs-ui/provider/next";
import { Metadata } from "next";
import "./global.css";

export const metadata: Metadata = {
  title: "LegoFactory",
  description:
    "A self-evolving LLM development pipeline composed of pluggable blocks: swegen → trajgen → sft → rl.",
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
