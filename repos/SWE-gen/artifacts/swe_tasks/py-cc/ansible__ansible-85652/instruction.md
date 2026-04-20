## Bug Report: Ansible 2.19.0 Breaks Template Handling for None Values and Multi-node String Concatenation

Two related regressions were introduced in Ansible 2.19.0 affecting template rendering:

### Issue 1: Multi-node loop templates fail with 'Provide a list of items/templates, or a template resolving to a list'

When using a multi-line Jinja2 template in a `loop:` directive that generates a list, Ansible 2.19.0 incorrectly rejects it. The following playbook worked in <=2.18 but fails in 2.19.0:

```yaml
- name: Test loop jinja template
  hosts: localhost
  vars:
    somevar:
      somefile1:
        typea:
          - mail1
          - mail2
        typeb:
          - mail3
  tasks:
  - debug:
      msg: "File: {{ item.file }} Type: {{ item.type }} mails: {{ item.mails }}"
    loop: |
      [
        {% for file, types in somevar.items() %}
          {% for type, mails in types.items() %}
            {"file": "{{ file }}", "type": "{{ type }}", "mails": {{ mails }} },
          {% endfor %}
        {% endfor %}
      ]
```

The template does resolve to a list when evaluated with a NativeEnvironment. The issue is that when templates are concatenated (multi-node), intermediate `None` values are being incorrectly handled during concatenation, causing the result type to be lost or corrupted.

Previous behavior: When concatenating multiple template nodes where some evaluate to `None`, those `None` values were treated as empty strings in the concatenation, preserving the overall string/list result.

New (broken) behavior: `None` values in concatenated template results are not handled correctly, causing type conversion failures.

### Issue 2: Empty template blocks render as `None` instead of empty string

Templates that render to an empty string (e.g., `{% if False %}{% endif %}`) now return `None` instead of an empty string in 2.19.0. This causes argument validation to fail with:

```
argument 'block' is of type NoneType and we were unable to convert to str: 'None' is not a string and conversion is not allowed
```

Example failing task:
```yaml
- blockinfile:
    path: output.txt
    block: "{% if False %}{% endif %}"
```

Expected: The template `{% if False %}{% endif %}` should resolve to an empty string `""`.
Actual: The template resolves to `None`, causing the `block` argument to fail type validation.

### Required Fixes

1. **Template concatenation (`concat`)**: When combining multiple template nodes into a string result, any node that evaluates to `None` should be treated as an empty string (as in <=2.18), so that multi-node templates don't lose their correct type or produce `None` instead of the expected concatenated result. Single-node template results should still preserve the native type (including `None` if that's what the template produced).

2. **Argument spec `str` type checking (`check_type_str`)**: The `check_type_str` function should treat `None` as an empty string `''` for backward compatibility, since in Ansible <=2.18, a template rendering to nothing would produce `''` rather than `None`. The `_check_type_str_no_conversion` (strict) variant should still reject `None`.

Specifically:
- `check_type_str(None)` should return `''` (relaxed, for backward compatibility)
- `_check_type_str_no_conversion(None)` should still raise `TypeError` with message containing `'is not a string and conversion is not allowed'`
- `check_type_str('string')` should return `'string'`
- `check_type_str(100)` should return `'100'`