# Python-Specific Review Rules

## Dead Code
- Unreachable code (after return/break/continue, always-false conditions)
- Unused imports and variables
- Commented-out code blocks

## Common Bugs
- **Mutable default arguments**: Never use `def f(x=[])` — use `def f(x=None)` with `x = x or []`
- **Bare except**: Never use `except:` — always catch specific exceptions (`except ValueError:`)
- **Shadowing builtins**: Don't name variables `list`, `dict`, `type`, `id`, `input`, `file`, `open`
- **f-string injection**: Don't use f-strings with user input in SQL queries or shell commands
- **Missing `self`**: Instance methods must have `self` as first parameter
- **Wrong comparison**: Use `is` for None/True/False, `==` for values

## Code Quality
- **Type hints**: Public functions should have type hints
- **List comprehensions**: Prefer over `map()`/`filter()` for simple transformations
- **Context managers**: Use `with` for file/network/resource handling
- **Pathlib**: Prefer `pathlib.Path` over `os.path` for path operations
- **f-strings**: Prefer over `.format()` or `%` formatting

## Security
- **No `eval()`/`exec()`** with untrusted input
- **No `pickle.loads()`** with untrusted data
- **No `subprocess.shell=True`** with user input
- **SQL**: Use parameterized queries, never string formatting
- **Secrets**: Don't hardcode API keys, passwords, tokens

## Performance
- **Generator expressions**: Use `(x for x in ...)` instead of `[x for x in ...` when only iterating
- **Avoid repeated lookups**: Cache `dict[key]` in a local variable if used multiple times
- **String concatenation**: Use `"".join()` for multiple concatenations in a loop
- **Global variables**: Avoid modifying module-level globals in functions
