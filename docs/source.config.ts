import {
  defineConfig,
  defineDocs,
  frontmatterSchema,
  metaSchema,
} from 'fumadocs-mdx/config';
import path from 'node:path';

const repoRoot = path.resolve(process.cwd(), '..');

export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export const curatorDocs = defineDocs({
  dir: path.join(repoRoot, 'blocks/curator/docs/content/docs'),
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export const tracerDocs = defineDocs({
  dir: path.join(repoRoot, 'blocks/tracer/docs/content/docs'),
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export const trainerDocs = defineDocs({
  dir: path.join(repoRoot, 'blocks/trainer/docs/content/docs'),
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export const evaluatorDocs = defineDocs({
  dir: path.join(repoRoot, 'blocks/evaluator/docs/content/docs'),
  docs: {
    schema: frontmatterSchema,
  },
  meta: {
    schema: metaSchema,
  },
});

export default defineConfig({
  mdxOptions: {},
});
