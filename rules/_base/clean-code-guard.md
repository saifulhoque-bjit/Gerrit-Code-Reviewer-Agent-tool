# Clean Code Guard — 23 Imperatives

## Functions and Names
1. **Names reveal intent.** Never use `data`, `result`, `item`, `temp`, `value`, `obj`, `info`, `helper`, `manager`, `utils` without a qualifier.
2. **Functions stay small.** Target ≤20 lines, one thing. If you can extract a function with a name that doesn't restate the body, the parent does too much.
3. **Four arguments max.** At five, introduce a request/config object. Never use boolean flag arguments — split into two functions.
4. **No output arguments.** A function either returns a value (query) or has a side effect (command), never both.

## Comments and Structure
5. **Comments explain WHY, never WHAT.** Delete comments that paraphrase the line below.
6. **Match existing style.** Read the file and a neighbor before writing. Mirror casing, imports, error handling.

## SOLID
7. **One actor per module.** A class answers to one stakeholder group. Split if two unrelated subsystems reach in.
8. **Extension via new code.** Adding a variant shouldn't require editing existing functions. Use registry/strategy/polymorphism.
9. **No subclass refuses its parent's contract.** Never override to signal "not implemented." If you need to, the inheritance is wrong.
10. **Abstractions live with the client.** Put interfaces in the consuming package, not next to the implementation.

## DRY, KISS, YAGNI
11. **Delete duplicated knowledge, not duplicated text.** Two similar functions encoding different rules are NOT a DRY violation.
12. **Wrong abstraction worse than duplication.** If an abstraction has accumulated special-case branches, re-inline it.
13. **Complexity ceiling: cyclomatic ≤10, nesting ≤5.**
14. **No speculative anything.** No optional parameter, config flag, or interface without a present-day caller.

## AI-Specific
15. **Never swallow errors with broad catch-all.** Catch specific types. If you can't recover, let it propagate.
16. **No defensive guards for impossible cases.** Trust the type contract.
17. **Verify every import and external call.** Don't generate code based on what the API "should" look like.
18. **No hardcoded success returns.** Never return `{"status": "ok"}` from a function that should do real work.
19. **Re-derive, don't copy from similar.** Off-by-one bugs enter through copy-paste-modify.
20. **Enumerate boundary cases before writing.** For any range, null/empty/one/many boundary, write the case list first.
21. **Strip dead code before delivery.** Remove unused imports, unreachable branches, "just in case" exports.
22. **Read before write.** Read the file you'll edit, one neighbor, and any project rules before writing.
23. **Preserve observable behavior when refactoring.** Same inputs → same outputs. Bug fixes and refactors are two separate operations.
