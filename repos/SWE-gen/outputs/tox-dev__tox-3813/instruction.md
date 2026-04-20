The `tox schema` command generates an incomplete JSON Schema that is missing important configuration keys. When running `tox schema` in a project with `no_package = true`, the generated schema omits packaging-related keys like `package`, `skip_install`, `use_develop`, `package_env`, and `package_root`. Additionally, the `int` type and `PythonConstraints` type are not properly handled and appear as unrecognized in logs.

The generated schema also lacks IDE integration metadata. Editors using taplo/Even Better TOML have no documentation links for tox configuration sections. Legacy aliases like `usedevelop`, `setenv`, `basepython` are not marked as deprecated, making it hard for users to know the canonical names.

The schema should:
1. Include all configuration keys regardless of the project's packaging mode by using a packaging-enabled config for introspection
2. Properly handle `int` type mapping to JSON Schema's `integer` type
3. Properly handle `PythonConstraints` type (used for Python version constraints)
4. Add `x-taplo` metadata with links to tox documentation for major sections
5. Mark legacy aliases as `"deprecated": true` with descriptions recommending canonical names
6. Use correct `$schema` URI for JSON Schema draft-07 compliance
7. Include proper `$id` pointing to the canonical schema URL
8. Support product dict format for `env_list` configuration
9. Use any available environment for schema introspection instead of hardcoding `"py"`

Users should be able to run `tox schema` and get a complete, IDE-friendly JSON Schema that covers all tox configuration options with proper documentation links and deprecation notices for legacy aliases.