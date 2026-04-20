Eleventy's built-in `slug` filter does not produce truly URL-safe slugs. When using the `slug` filter on strings containing apostrophes or other special characters, the resulting slug is not safe for use in URLs.

For example, using `{{ "it's a test" | slug }}` produces `it's-a-test`, which when rendered in a URL becomes `/tags/it&#39;s-a-test/` — the apostrophe is HTML-encoded, breaking the URL and producing ugly output. The `slug` filter also fails to handle other special characters like `♥`, Cyrillic characters (e.g., `люблю`), and symbols like `$#%`.

The fix is to add a new `slugify` filter that serves as a better, URL-safe alternative to the existing `slug` filter. The new `slugify` filter should:

1. Be available in all template engines that support filters (Nunjucks, Liquid, Handlebars, etc.)
2. Properly handle apostrophes and other special characters, stripping them rather than leaving them in the output
3. Handle Unicode characters including Cyrillic letters (e.g., `люблю`)
4. Handle symbols like `♥`, `$`, `#`, `%`, `-`
5. Handle mixed case (e.g., `Hi, I'm ZAch` → `hi-im-zach`)
6. Accept optional configuration options, similar to how `slug` accepts options

Specific expected behavior:
- `"Hi, I'm ZAch" | slugify` → `hi-im-zach` (apostrophe and comma removed, decamelized by default)
- `"_Slug ♥ CANDIDATE люблю $#%-" | slugify` → `slug-candidate-lyublyu` (symbols and special chars removed)
- `"Hi, I'm ZAch" | slugify({decamelize: true})` → `hi-im-z-ach` (with decamelize option)
- The existing `slug` filter should remain functional and still accept its own options (e.g., `| slug({replacement:'_'})` → uses underscore as separator)

The `slugify` filter should use the `@sindresorhus/slugify` package (or equivalent) rather than the existing `slugify` npm package used by `slug`. The filter should be registered alongside `slug` in the universal filters configuration so it's available across all supported template languages.

The `slug` filter is considered deprecated in favor of `slugify`, but must remain for backward compatibility.