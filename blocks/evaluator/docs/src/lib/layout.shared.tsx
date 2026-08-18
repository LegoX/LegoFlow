import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <div className="flex items-center gap-2 mr-4">
          <p className="font-mono tracking-tight text-lg font-normal">
            eval
          </p>
        </div>
      ),
    },
    githubUrl: "https://github.com/LegoX/SWE-Lego-Live",
    links: [
      {
        url: "/docs",
        text: "docs",
        active: "nested-url",
      },
      ...(process.env.NEXT_PUBLIC_DASHBOARD_URL
        ? [
            {
              url: process.env.NEXT_PUBLIC_DASHBOARD_URL,
              text: "dashboard",
              active: "none" as const,
              external: true,
            },
          ]
        : []),
    ],
    themeSwitch: {
      enabled: true,
      mode: "light-dark-system",
    },
  };
}
