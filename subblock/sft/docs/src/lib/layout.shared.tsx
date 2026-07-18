import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <div className="flex items-center gap-2 mr-4">
          <p className="font-mono tracking-tight text-lg font-normal">
            sft
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
    ],
    themeSwitch: {
      enabled: true,
      mode: "light-dark-system",
    },
  };
}
