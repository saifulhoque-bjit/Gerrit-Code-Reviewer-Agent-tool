# Java-Specific Review Rules

## Typos and Spelling
- Spelling errors in variable/method/class names at declaration sites
- Spelling errors in log messages or exception messages affecting readability
- Do NOT report spelling errors at reference sites (determined by declaration)

## Dead Code
- Unreachable code blocks (always-false conditions, code after return)
- Variables declared but never read
- Large blocks of commented-out code

## Logic Errors
- Incorrect if-condition logic (read surrounding context to confirm)
- Boundary condition errors (index and array length checks)
- Boolean logic operator misuse (precedence and short-circuit issues)
- Infinite loops or recursion without termination
- Missing break in switch cases (unintended fall-through)
- NPE-prone patterns (inspect data source call chain before flagging)
- Missing parentheses changing execution order

## Performance
- Database queries inside loops (search for DB operations to confirm)
- N+1 query problems (suggest batch optimization)
- Large datasets without pagination
- O(n²) or worse algorithms where better options exist

## Thread Safety
Only flag when:
- **Race conditions**: check-then-act patterns vulnerable to intermediate state changes
- **Non-atomic compound ops**: multi-step operations needing atomicity without synchronization
- **Unsafe lazy init**: broken double-checked locking in singletons/caches
- **Concurrent writes to unsafe collections**: ArrayList/HashMap modified in multi-threaded context

Do NOT flag when:
- Local variables (inherently thread-safe)
- Single-threaded context (confirm via code_search)
- Read-only operations on non-thread-safe structures
- Immutable objects / final fields
- Proper synchronization already in place (synchronized, Lock, atomic)
- Single-threaded components (Builder pattern build phase, DTOs)
