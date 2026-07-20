# Rust Code Review Rules

## unwrap()/expect() in Production
- Flag every `.unwrap()` and `.expect()` in src/ (not tests/examples/benches) — use `?` or context
- Exception: documented invariant with comment explaining why it can't fail

## Lifetime Elision Pitfalls
- Functions returning a reference with two+ reference parameters need explicit lifetime annotations
- Elision picks first input lifetime — ambiguous with multiple inputs

## Mutex Deadlock Patterns
- Holding std::sync::Mutex lock across `.await` — use tokio::sync::Mutex or clone data out first
- Acquiring multiple locks in inconsistent order — require consistent ordering or single-lock design
- Re-entrant lock on same Mutex (deadlock) — use RwLock or restructure

## Async Cancellation Safety
- Inside tokio::select!, branches with multiple `.await` after a resource-consuming op (recv/read) — message may be lost; final `.await` must be the commit point

## Unsafe Block Auditing
- Every `unsafe` block and `unsafe impl` must have a `// SAFETY:` comment within 3 lines above
- Audit that claimed invariants are actually upheld by surrounding code

## Unnecessary Allocations
- `collect::<Vec<_>>()` followed by `.iter()` in same scope — use lazy iterator chain instead
- `.to_vec()` or `.clone()` on a slice just to iterate — use `.iter()` directly

## Error Handling: anyhow vs thiserror
- `anyhow::Result` in library pub API → flag (caller can't match on variants, use thiserror)
- `thiserror` enums in main.rs/bin/ with unmatched variants → flag (use anyhow for apps)
- Libraries: thiserror. Applications/binaries: anyhow.

## Send + Sync Bounds
- Rc, RefCell, Cell, raw pointers in Send-required contexts (tokio::spawn) — use Arc, Mutex
- Generic futures/tasks without `+ Send` bound when passed to `spawn`

## Drop Order
- Structs with multiple Drop-implementing fields where drop order matters — declare in reverse-dependency order
- Manual `drop()` calls creating dependency inversions

## clone() Abuse
- `.clone()` on Vec, String, HashMap in loops or hot paths — restructure ownership instead
- Require comment justifying clone if in hot path
