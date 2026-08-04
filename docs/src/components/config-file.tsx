import fs from 'node:fs';
import path from 'node:path';

type ConfigFileProps = {
  file: string;
};

type ConfigSnippetProps = {
  children: string | string[];
  title?: string;
};

function highlightYaml(line: string) {
  const commentIndex = line.indexOf('#');
  const body = commentIndex >= 0 ? line.slice(0, commentIndex) : line;
  const comment = commentIndex >= 0 ? line.slice(commentIndex) : '';
  const keyMatch = body.match(/^(\s*)([A-Za-z0-9_.-]+)(\s*:)(.*)$/);

  if (!keyMatch) {
    return (
      <>
        {body}
        {comment && <span className="text-emerald-700">{comment}</span>}
      </>
    );
  }

  const [, indent, key, colon, value] = keyMatch;
  const trimmed = value.trim();
  let valueClass = 'text-fd-foreground';

  if (/^["'].*["']$/.test(trimmed)) {
    valueClass = 'text-sky-700';
  } else if (/^(true|false|null)\b/.test(trimmed)) {
    valueClass = 'text-purple-700';
  } else if (/^-?\d+(\.\d+)?([eE][+-]?\d+)?\b/.test(trimmed)) {
    valueClass = 'text-amber-700';
  } else if (/^\[.*\]$/.test(trimmed) || /^\{.*\}$/.test(trimmed)) {
    valueClass = 'text-indigo-700';
  }

  return (
    <>
      {indent}
      <span className="font-semibold text-[var(--swe-live-accent)]">{key}</span>
      <span>{colon}</span>
      <span className={valueClass}>{value}</span>
      {comment && <span className="text-emerald-700">{comment}</span>}
    </>
  );
}

function ConfigCodeBlock({ content, title }: { content: string; title: string }) {
  return (
    <figure className="not-prose my-5 overflow-hidden rounded-lg border border-fd-border bg-fd-muted">
      <figcaption className="border-b border-fd-border px-4 py-2 text-sm font-medium text-fd-muted-foreground">
        {title}
      </figcaption>
      <pre className="overflow-x-auto p-4 text-sm leading-6">
        <code>
          {content.split('\n').map((line, index) => (
            <span key={index} className="block">
              {highlightYaml(line)}
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}

export function ConfigFile({ file }: ConfigFileProps) {
  const repoRoot = path.resolve(process.cwd(), '..');
  const absolutePath = path.resolve(repoRoot, file);
  const relativePath = path.relative(repoRoot, absolutePath);
  const content = fs.readFileSync(absolutePath, 'utf8');

  return <ConfigCodeBlock content={content} title={relativePath} />;
}

export function ConfigSnippet({ children, title = 'config.yaml' }: ConfigSnippetProps) {
  const content = Array.isArray(children) ? children.join('') : children;

  return <ConfigCodeBlock content={content.trim()} title={title} />;
}
