import { source } from '@/lib/source';
import {
  DocsBody,
  DocsDescription,
  DocsPage,
  DocsTitle,
} from 'fumadocs-ui/page';
import { notFound } from 'next/navigation';
import { getMDXComponents } from '@/mdx-components';
import type { Metadata } from 'next';
import { createRelativeLink } from 'fumadocs-ui/mdx';
import type { ComponentProps } from 'react';

const blockDocsPrefix = '/docs/blocks/';

function rewriteBlockDocsHref(currentUrl: string, href?: string) {
  if (!href?.startsWith('/docs/') || href.startsWith(blockDocsPrefix)) {
    return href;
  }

  const [, block] = currentUrl.slice(blockDocsPrefix.length).split('/');
  if (currentUrl.startsWith(blockDocsPrefix) && block !== 'evaluator') {
    return `${blockDocsPrefix}${block}${href.slice('/docs'.length)}`;
  }

  return href;
}

export default async function Page(props: PageProps<'/docs/[[...slug]]'>) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  const MDX = page.data.body;
  const RelativeLink = createRelativeLink(source as any, page as any);
  const BlockAwareLink = (props: ComponentProps<'a'>) => (
    <RelativeLink
      {...props}
      href={rewriteBlockDocsHref(page.url, props.href)}
    />
  );

  return (
    <DocsPage toc={page.data.toc} full={page.data.full}>
      <DocsTitle>{page.data.title}</DocsTitle>
      <DocsDescription>{page.data.description}</DocsDescription>
      <DocsBody>
        <MDX
          components={getMDXComponents({
            a: BlockAwareLink,
          })}
        />
      </DocsBody>
    </DocsPage>
  );
}

export async function generateStaticParams() {
  return source.generateParams();
}

export async function generateMetadata(
  props: PageProps<'/docs/[[...slug]]'>,
): Promise<Metadata> {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  return {
    title: page.data.title,
    description: page.data.description,
  };
}
