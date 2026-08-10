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
  return (
    <>
      {indent}
      <span className="font-semibold text-fd-primary">{key}</span>
      <span>{colon}</span>
      <span>{value}</span>
      {comment && <span className="text-emerald-700">{comment}</span>}
    </>
  );
}

export function ConfigFile({ file }: ConfigFileProps) {
  const repoRoot = path.resolve(process.cwd(), '../../..');
  const absolutePath = path.resolve(repoRoot, file);
  const relativePath = path.relative(repoRoot, absolutePath);
  const content = fs.readFileSync(absolutePath, 'utf8');

  return (
    <details
      open
      className="not-prose my-5 overflow-hidden rounded-lg border border-fd-border bg-fd-muted"
    >
      <summary className="cursor-pointer border-b border-fd-border px-4 py-2 text-sm font-medium text-fd-muted-foreground">
        {relativePath}
      </summary>
      <pre className="overflow-x-auto p-4 text-sm leading-6">
        <code>
          {content.split('\n').map((line, index) => (
            <span key={index} className="block">
              {highlightYaml(line)}
            </span>
          ))}
        </code>
      </pre>
    </details>
  );
}

export function ConfigSnippet({
  children,
  title = 'config.yaml',
}: ConfigSnippetProps) {
  const content = Array.isArray(children) ? children.join('') : children;
  return (
    <figure className="not-prose my-5 overflow-hidden rounded-lg border border-fd-border bg-fd-muted">
      <figcaption className="border-b border-fd-border px-4 py-2 text-sm font-medium text-fd-muted-foreground">
        {title}
      </figcaption>
      <pre className="overflow-x-auto p-4 text-sm leading-6">
        <code>
          {content
            .trim()
            .split('\n')
            .map((line, index) => (
              <span key={index} className="block">
                {highlightYaml(line)}
              </span>
            ))}
        </code>
      </pre>
    </figure>
  );
}
