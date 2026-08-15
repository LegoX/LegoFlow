import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <div className="flex items-center gap-2 mr-4">
          <p className="docs-brand-title">
            Lego
            <span className="docs-live-word">
              Flow
              <span
                className="docs-live-pulse-dot"
                aria-hidden="true"
              />
            </span>
          </p>
        </div>
      ),
    },
    githubUrl: "https://github.com/LegoX/SWE-Lego-Live",
    links: [
      {
        url: "https://legoflow.pages.dev",
        text: "home",
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
