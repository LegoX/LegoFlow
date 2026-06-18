import { RootProvider } from "fumadocs-ui/provider/next";
import { Metadata } from "next";
import "./global.css";

export const metadata: Metadata = {
  title: "Verl-SWE-RL",
  description:
    "Online RL (PPO/GRPO/GSPO) for SWE coding agents — verl + Harbor + vLLM.",
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
