import { curatorDocs, docs, evaluatorDocs, terminalCuratorDocs, tracerDocs, trainerDocs } from '@/.source/server';
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
  'terminal-curator': loader({
    baseUrl: '/docs/blocks/terminal-curator',
    source: terminalCuratorDocs.toFumadocsSource(),
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
  'terminal-curator': 'Terminal Curator',
  tracer: 'Tracer',
  trainer: 'Trainer',
  evaluator: 'Evaluator',
};

function isBlockName(value: string | undefined): value is BlockName {
  return (
    value === 'curator' ||
    value === 'terminal-curator' ||
    value === 'tracer' ||
    value === 'trainer' ||
    value === 'evaluator'
  );
}

// Match a complete namespace, not names such as terminal-curator-extra.
function mountedBlock(href: string | undefined): BlockName | undefined {
  const pathname = href?.split(/[?#]/, 1)[0];
  if (!pathname) return undefined;
  return (Object.keys(blockSources) as BlockName[]).find((name) => {
    const base = `/docs/blocks/${name}`;
    return pathname === base || pathname.startsWith(`${base}/`);
  });
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
  // An explicit link to any mounted block already has its final namespace.
  // In particular, a cross-block link must not be prefixed with this block.
  if (mountedBlock(href)) return href;

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

  // Keep unknown explicit block routes intact rather than inventing a target.
  if (href.startsWith('/docs/blocks/')) return href;

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
          blockFolder('terminal-curator'),
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
    const block = mountedBlock(parent.url);
    return block ? resolveBlockHref(block, href, parent) : rootSource.resolveHref(href, parent);
  },

  getPageByHref(href: string, options?: any) {
    const block = mountedBlock(href);
    // Each block loader was created with the mounted baseUrl. Removing that
    // prefix before lookup makes every otherwise valid mounted href miss.
    return block
      ? blockSources[block].getPageByHref(href, options)
      : rootSource.getPageByHref(href, options);
  },
};
