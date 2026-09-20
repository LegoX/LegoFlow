import defaultMdxComponents from 'fumadocs-ui/mdx';
import type { MDXComponents } from 'mdx/types';
import { ConfigFile, ConfigSnippet } from './components/config-file';

export function getMDXComponents(components?: MDXComponents): MDXComponents {
  return {
    ...defaultMdxComponents,
    ConfigFile,
    ConfigSnippet,
    ...components,
  };
}
