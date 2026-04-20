Implement support for custom (native Rust) filters in jaq-core so that users can define filters backed by Rust closures rather than pure jaq definitions.

Currently, there is no way to register a `Filter` that calls Rust code without forking the library. Users who embed jaq in their applications need to expose application-specific functionality (e.g., `string | store("key")` or `fetch("key") -> string`) as jaq filters implemented in Rust.

## What needs to be implemented

### `CustomFilter` type

Add a `CustomFilter` struct that wraps a Rust closure and can be registered with the filter system. It should support:

1. **`CustomFilter::new(arity, run_fn)`** — Creates a non-updatable custom filter. `arity` is the number of filter arguments (e.g., `0` for `sqrt`, `1` for `iflonger(f)`). The `run_fn` receives the filter arguments and the current `(ctx, val)` pair and returns a boxed iterator of `Result<Val, Error>` values.

2. **`CustomFilter::with_update(arity, run_fn, update_fn)`** — Creates a custom filter that can also be used in update position (e.g., `filter |= expr`). The `update_fn` receives the filter arguments, the current `(ctx, val)` pair, and the update expression.

### `Definitions.insert_custom(name, filter)`

Add an `insert_custom` method to `Definitions` that registers a `CustomFilter` under the given name, making it available for use in jaq expressions.

### New error variants

Add two new variants to the `Error` type:
- `Error::NonUpdatable` — Returned when a custom filter created without an update function is used in update position (e.g., `nupd |= .`).
- `Error::Custom(String)` — A general-purpose error for use by custom filter implementations to report domain-specific errors.

### Derive traits on `Definitions`

Add `Clone`, `Debug`, and `Default` derives to `Definitions` so it can be used more ergonomically (e.g., cloned in test helpers or multi-call scenarios).

## Expected behavior examples

**Arity-0 source filter:**
```rust
defs.insert_custom("natzero", CustomFilter::new(0, |_, _cv| {
    Box::new(std::iter::once(Ok(Val::Int(0))))
}));
// null | natzero  =>  0
```

**Arity-0 filter with input transformation:**
```rust
defs.insert_custom("str_rev", CustomFilter::new(0, |_, (_, val)| {
    Box::new(std::iter::once(
        val.to_str().map(|s| Val::str(s.chars().rev().collect()))
    ))
}));
// "hello" | str_rev  =>  "olleh"
```

**Non-updatable filter raises `NonUpdatable` in update position:**
```rust
defs.insert_custom("nupd", CustomFilter::new(0, |_, (_, val)| {
    Box::new(std::iter::once(Ok(val)))
}));
// "hello" | nupd        =>  "hello"  (OK)
// "hello" | nupd |= .   =>  Error::NonUpdatable
```

**Updatable filter works in update position:**
```rust
defs.insert_custom("with_length", CustomFilter::with_update(
    0,
    |_, (_, val)| Box::new(std::iter::once(val.to_str().map(|s| Val::from(s.len())))),
    |_, (_, val), _| Box::new(std::iter::once(val.to_str().map(|s| Val::from(s.len())))),
));
// "hello" | with_length       =>  5
// "hello" | with_length |= .  =>  5
```

**Arity-1 filter with filter argument:**
```rust
defs.insert_custom("iflonger", CustomFilter::new(1, |args, (ctx, val)| {
    // args[0] is a filter; apply it to determine the threshold
    // if val (string) is longer than threshold, yield val, else yield null
    ...
}));
// "hello" | iflonger(3)  =>  "hello"
// "hi"    | iflonger(3)  =>  null
```

The `run_fn` and `update_fn` closures receive the filter arguments as a slice so they can be invoked like normal sub-filters.