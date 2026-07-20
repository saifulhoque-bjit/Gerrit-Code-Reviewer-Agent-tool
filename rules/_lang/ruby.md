# Ruby Code Review Rules

## SQL Injection
- SQL strings with `#{}` interpolation or `+` concatenation with user values — use parameterized queries: `where(name: val)` or `where("name = ?", val)`

## Mass Assignment
- `.create(params[...])` or `.update(params[...])` without `.permit(...)` — use strong parameters

## YAML.load vs YAML.safe_load
- `YAML.load` on external/user data — RCE vector; use `YAML.safe_load` with permitted_classes

## eval / instance_eval — Code Injection
- eval/instance_eval/class_eval receiving a string built from external input — use send, const_get, or block-based define_method

## Frozen String Literals
- Missing `# frozen_string_literal: true` in new files
- `<<` on string literals without `+` prefix (mutable copy)

## Monkey Patching
- Reopening core classes (String, Array, Hash) to redefine existing methods — use refinements or wrapper modules

## Thread Safety
- Shared instance/class variables mutated across threads without Mutex, Monitor, or Concurrent::*

## Nil Guard
- Deep method chains on potentially nil values (params, associations) — use `&.` safe navigation operator

## Exception Handling
- `rescue Exception` catches signals, SystemExit, NoMemoryError — use `rescue StandardError` or specific classes

## N+1 Queries
- `.each`/`.map` loops accessing `record.association` not eager-loaded — use `includes`/`preload`
