import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <div className="flex items-center gap-2 mr-4">
          <p className="font-mono tracking-tight text-lg font-normal">
            Verl-SWE-RL
          </p>
        </div>
      ),
    },
    githubUrl: "https://github.com/SWE-Lego/SWE-Lego-Live",
    links: [
      {
        url: "/docs",
        text: "docs",
        active: "nested-url",
      },
      {
        // Stable training-dashboard URL once published to Cloudflare Pages.
        // For local/ephemeral viewing, serve it via `dashboard/serve.sh`.
        url: "https://swe-lego-rl-dashboard.pages.dev",
        text: "dashboard",
        active: "none",
        external: true,
      },
    ],
    themeSwitch: {
      enabled: true,
      mode: "light-dark-system",
    },
  };
}
