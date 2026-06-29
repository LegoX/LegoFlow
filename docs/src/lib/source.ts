import { curatorDocs, docs, tracerDocs, trainerDocs } from '@/.source/server';
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
    baseUrl: '/docs/sub-block/curator',
    source: curatorDocs.toFumadocsSource(),
    plugins,
  }),
  tracer: loader({
    baseUrl: '/docs/sub-block/tracer',
    source: tracerDocs.toFumadocsSource(),
    plugins,
  }),
  trainer: loader({
    baseUrl: '/docs/sub-block/trainer',
    source: trainerDocs.toFumadocsSource(),
    plugins,
  }),
};

type BlockName = keyof typeof blockSources;

function isBlockName(value: string | undefined): value is BlockName {
  return value === 'curator' || value === 'tracer' || value === 'trainer';
}

function blockFolder(name: BlockName) {
  const tree = blockSources[name].pageTree;

  return {
    type: 'folder',
    name,
    collapsible: true,
    defaultOpen: false,
    children: tree.children,
  };
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
          blockFolder('curator'),
          blockFolder('tracer'),
          blockFolder('trainer'),
          ...node.children.filter(
            (child: any) =>
              child.type === 'page' &&
              child.url === '/docs/sub-block/evaluator',
          ),
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

    if (first === 'sub-block' && isBlockName(second)) {
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
              page.slugs[0] === 'sub-block' &&
              isBlockName(page.slugs[1])
            ),
        ),
      ...Object.entries(blockSources).flatMap(([name, blockSource]) =>
        blockSource.getPages().map((page) => ({
          ...page,
          slugs: ['sub-block', name, ...page.slugs],
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
              param.slug[0] === 'sub-block' &&
              isBlockName(param.slug[1])
            ),
        ),
      ...Object.entries(blockSources).flatMap(([name, blockSource]) =>
        blockSource
          .generateParams()
          .map((param) => ({ slug: ['sub-block', name, ...param.slug] })),
      ),
    ];
  },

  resolveHref(href: string, parent: any) {
    if (parent.url?.startsWith('/docs/sub-block/curator')) {
      return blockSources.curator.resolveHref(href, parent);
    }

    if (parent.url?.startsWith('/docs/sub-block/tracer')) {
      return blockSources.tracer.resolveHref(href, parent);
    }

    if (parent.url?.startsWith('/docs/sub-block/trainer')) {
      return blockSources.trainer.resolveHref(href, parent);
    }

    return rootSource.resolveHref(href, parent);
  },

  getPageByHref(href: string, options?: any) {
    if (href.startsWith('/docs/sub-block/curator')) {
      return blockSources.curator.getPageByHref(
        href.replace('/docs/sub-block/curator', '/docs'),
        options,
      );
    }

    if (href.startsWith('/docs/sub-block/tracer')) {
      return blockSources.tracer.getPageByHref(
        href.replace('/docs/sub-block/tracer', '/docs'),
        options,
      );
    }

    if (href.startsWith('/docs/sub-block/trainer')) {
      return blockSources.trainer.getPageByHref(
        href.replace('/docs/sub-block/trainer', '/docs'),
        options,
      );
    }

    return rootSource.getPageByHref(href, options);
  },
};
