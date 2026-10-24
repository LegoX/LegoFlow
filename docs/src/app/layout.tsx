import { RootProvider } from "fumadocs-ui/provider/next";
import { Metadata } from "next";
import "./global.css";

export const metadata: Metadata = {
  title: "LegoFlow",
  description:
    "A self-evolving LLM development pipeline with SWE or terminal task curation, trajectory generation, training, and evaluation.",
};

export default function Layout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="flex flex-col min-h-screen">
        <RootProvider search={{ options: { type: "static" } }}>
          {children}
        </RootProvider>
      </body>
    </html>
  );
}
