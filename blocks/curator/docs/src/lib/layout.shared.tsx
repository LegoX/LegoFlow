import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <div className="flex items-center gap-2 mr-4">
          <p className="font-mono tracking-tight text-lg font-normal">
            LegoFlow Curator
          </p>
        </div>
      ),
    },
    githubUrl: "https://github.com/LegoX/LegoFlow",
    links: [
      {
        url: "/docs",
        text: "docs",
        active: "nested-url",
      },
      {
        // Deliberately not a deployment URL: every operator publishes to their
        // own Cloudflare account, and the address is only known after a deploy.
        url: "/docs/dashboard",
        text: "dashboard",
        active: "nested-url",
      },
    ],
    themeSwitch: {
      enabled: true,
      mode: "light-dark-system",
    },
  };
}
