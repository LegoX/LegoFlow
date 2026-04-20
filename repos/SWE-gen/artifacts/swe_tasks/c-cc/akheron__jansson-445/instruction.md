There is a segmentation fault / core dump when calling `json_pack` with certain malformed format strings.

A minimal reproducing example:

```c
#include <jansson.h>

int main(int argc, char const *argv[])
{
    const char json_format[6] = "[1s";
    const char json_data[10]  = "Hi";

    json_pack(json_format, json_data);

    return(0);
}
```

This crashes with:
```
*** Error in `./bug03': munmap_chunk(): invalid pointer: 0x000000000040077a ***
Aborted (core dumped)
```

The root causes are two bugs in the pack implementation:

1. **`pack_string` incorrectly frees non-owned memory**: The internal `pack_string` function has an inverted condition for the `ours` flag check. When a packing error occurs, it attempts to free a string pointer that was passed in directly by the caller (i.e., a stack/static string), rather than one that was dynamically allocated. The check for whether the string is owned (`ours`) is backwards, causing `jsonp_free` to be called on an invalid pointer — hence the crash.

2. **`json_vpack_ex` has an unreachable/incorrect error check**: There is an error check for `s.has_error` that can never be true unless `value` is NULL, leading to incorrect early-exit behavior in some error paths.

Expected behavior: Calling `json_pack` with a malformed or truncated format string (e.g., `"[1s"`, an empty string `""`, or a format that produces a NULL string mid-pack) should return NULL and report a proper error via `json_error_t`, without crashing or freeing invalid pointers.

Specific cases that must not crash and should return NULL:
- `json_pack("")` — empty format string
- `json_pack("{s:s}", "key", NULL)` — NULL object value with non-null key
- `json_pack("[ss]", NULL, "value")` — array containing a non-null string after a NULL string causes error
- `json_pack("[1s", "Hi")` — malformed array format string

The fix must ensure that `pack_string` never calls `jsonp_free` on a string it does not own, and that `json_vpack_ex` does not prematurely error out on valid non-NULL values.