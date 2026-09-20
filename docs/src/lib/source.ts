import { curatorDocs, docs, evaluatorDocs, tracerDocs, trainerDocs } from '@/.source/server';
import { loader } from 'fumadocs-core/source';
import { lucideIconsPlugin } from 'fumadocs-core/source/lucide-icons';

// See https://fumadocs.dev/docs/headless/source-api for more info
const plugins = [lucideIconsPlugin()];

const rootSource = loader({
  baseUrl: '/docs',
  source: docs.toFumadocsSource(),
  plugins,
});

const blockSources = {
  curator: loader({
    baseUrl: '/docs/blocks/curator',
    source: curatorDocs.toFumadocsSource(),
    plugins,
  }),
  tracer: loader({
    baseUrl: '/docs/blocks/tracer',
    source: tracerDocs.toFumadocsSource(),
    plugins,
  }),
  trainer: loader({
    baseUrl: '/docs/blocks/trainer',
    source: trainerDocs.toFumadocsSource(),
    plugins,
  }),
  evaluator: loader({
    baseUrl: '/docs/blocks/evaluator',
    source: evaluatorDocs.toFumadocsSource(),
    plugins,
  }),
};

type BlockName = keyof typeof blockSources;

const blockLabels: Record<BlockName, string> = {
  curator: 'Curator',
  tracer: 'Tracer',
  trainer: 'Trainer',
  evaluator: 'Evaluator',
};

function isBlockName(value: string | undefined): value is BlockName {
  return (
    value === 'curator' ||
    value === 'tracer' ||
    value === 'trainer' ||
    value === 'evaluator'
  );
}

function blockFolder(name: BlockName) {
  const tree = blockSources[name].pageTree;

  return {
    type: 'folder',
    name: blockLabels[name],
    collapsible: true,
    defaultOpen: false,
    children: tree.children,
  };
}

function resolveBlockHref(
  name: BlockName,
  href: string,
  parent: any,
) {
  // Block prose is shared with each standalone docs site, where internal
  // links use /docs/.... When mounted in the root site, preserve the block
  // namespace instead of letting fumadocs resolve against the mounted page's
  // final slug (which can produce /docs/blocks/<page>/...).
  const blockBase = `/docs/blocks/${name}`;
  if (href === blockBase || href.startsWith(`${blockBase}/`) || href.startsWith(`${blockBase}#`)) {
    return href;
  }

  // fumadocs may already have resolved a standalone /docs/... link against
  // the mounted page before this hook runs, yielding
  // /docs/blocks/<current-section>/<target>. Strip that synthetic current
  // section before applying the real block namespace.
  const parentPath = String(parent.url ?? '').slice(blockBase.length);
  const currentSection = parentPath.split('/').filter(Boolean)[0];
  const syntheticBase = `/docs/blocks/${currentSection ?? 'undefined'}`;
  if (
    (href === syntheticBase ||
      href.startsWith(`${syntheticBase}/`) ||
      href.startsWith(`${syntheticBase}#`))
  ) {
    return `${blockBase}${href.slice(syntheticBase.length)}`;
  }

  if (href === '/docs' || href.startsWith('/docs/') || href.startsWith('/docs#')) {
    return `${blockBase}${href.slice('/docs'.length)}`;
  }

  return blockSources[name].resolveHref(href, parent);
}

function mergedPageTree() {
  const tree = rootSource.pageTree;

  return {
    ...tree,
    children: tree.children.map((node: any) => {
      if (node.type !== 'folder' || node.name !== 'Blocks') return node;

      return {
        ...node,
        children: [
          ...(node.children ?? []),
          blockFolder('curator'),
          blockFolder('tracer'),
          blockFolder('trainer'),
          blockFolder('evaluator'),
        ],
      };
    }),
  };
}

export const source = {
  ...rootSource,

  get pageTree() {
    return mergedPageTree();
  },

  getPage(slugs?: string[]) {
    const [first, second, third, ...rest] = slugs ?? [];

    if (first === 'blocks' && isBlockName(second)) {
      return blockSources[second].getPage(third ? [third, ...rest] : []);
    }

    return rootSource.getPage(slugs);
  },

  getPages() {
    return [
      ...rootSource
        .getPages()
        .filter(
          (page) =>
            !(
              page.slugs[0] === 'blocks' &&
              isBlockName(page.slugs[1])
            ),
        ),
      ...Object.entries(blockSources).flatMap(([name, blockSource]) =>
        blockSource.getPages().map((page) => ({
          ...page,
          slugs: ['blocks', name, ...page.slugs],
        })),
      ),
    ];
  },

  generateParams() {
    return [
      ...rootSource
        .generateParams()
        .filter(
          (param) =>
            !(
              param.slug[0] === 'blocks' &&
              isBlockName(param.slug[1])
            ),
        ),
      ...Object.entries(blockSources).flatMap(([name, blockSource]) =>
        blockSource
          .generateParams()
          .map((param) => ({ slug: ['blocks', name, ...param.slug] })),
      ),
    ];
  },

  resolveHref(href: string, parent: any) {
    if (parent.url?.startsWith('/docs/blocks/curator')) {
      return resolveBlockHref('curator', href, parent);
    }

    if (parent.url?.startsWith('/docs/blocks/tracer')) {
      return resolveBlockHref('tracer', href, parent);
    }

    if (parent.url?.startsWith('/docs/blocks/trainer')) {
      return resolveBlockHref('trainer', href, parent);
    }

    if (parent.url?.startsWith('/docs/blocks/evaluator')) {
      return resolveBlockHref('evaluator', href, parent);
    }

    return rootSource.resolveHref(href, parent);
  },

  getPageByHref(href: string, options?: any) {
    if (href.startsWith('/docs/blocks/curator')) {
      return blockSources.curator.getPageByHref(
        href.replace('/docs/blocks/curator', '/docs'),
        options,
      );
    }

    if (href.startsWith('/docs/blocks/tracer')) {
      return blockSources.tracer.getPageByHref(
        href.replace('/docs/blocks/tracer', '/docs'),
        options,
      );
    }

    if (href.startsWith('/docs/blocks/trainer')) {
      return blockSources.trainer.getPageByHref(
        href.replace('/docs/blocks/trainer', '/docs'),
        options,
      );
    }

    if (href.startsWith('/docs/blocks/evaluator')) {
      return blockSources.evaluator.getPageByHref(
        href.replace('/docs/blocks/evaluator', '/docs'),
        options,
      );
    }

    return rootSource.getPageByHref(href, options);
  },
};
