There is an off-by-one error in the token range calculation that causes autocomplete to fail for certain formatting patterns in GraphQL template literals.

When a GraphQL query or mutation is written with specific spacing (particularly when content starts on a new line after the opening backtick with specific indentation), autocomplete suggestions are not being proposed. For example:

```tsx
const mutationDoc = graphql(`mutation MyMutation {
  
}
`)
```

In this case, trying to get suggestions for mutation names on the empty line returns no autocomplete results. However, if a space is added before the closing braces, autocomplete starts working.

The issue is related to an inconsistency in how token start positions are calculated. The `stream.getStartOfToken()` method should be used consistently with `+ 1` offset across all token range calculations. Currently, some code paths may be using `stream.getStartOfToken()` without the `+ 1` adjustment, causing the token range to be off by one character.

This regression affects:
1. Autocomplete/completion info requests for selection-sets inside GraphQL operations
2. Hover information when hovering over the first character of a field

Expected behavior: Autocomplete should work consistently regardless of formatting/spacing within GraphQL template literals. Hover information should display correctly when hovering over any character of a field, including the first character.