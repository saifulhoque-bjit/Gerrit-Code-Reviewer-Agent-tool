# TypeScript / JavaScript Review Rules

## Typos
- Spelling errors in variable/function/component/Props names at declaration
- Spelling in log/error messages affecting readability

## Dead Code
- Unreachable code (always-false conditions, code after return)
- Variables declared but never read
- Commented-out code blocks

## Code Quality
- **No `var`**: Use `let` or `const`
- **Strict equality**: Use `===` and `!==`, never `==`/`!=`
- **No `any` type**: If unavoidable, explain with a comment
- **Null checks**: Guard against null/undefined before accessing properties
- **No nested ternaries**: Use if/else or early returns instead
- **No hardcoding**: Business URLs, paths, and numbers should be configurable
- **Extract duplicate logic**: Common patterns should be shared functions

## React (if applicable)
- Hooks only at top level, only in React functions
- useEffect must handle dependencies and cleanup
- No side effects in render (API calls, DOM manipulation)
- No inline `style` except for truly dynamic values
- Don't declare components inside components (use render methods)
- Use React.memo/useMemo/useCallback when performance matters

## Async
- All async functions must have error handling
- Prefer async/await over .then() chains
- Use `Promise.all` for independent parallel async operations
- Sequential execution only for dependent operations

## Security
- Escape user input (XSS protection)
- Never use `innerHTML` with user input
- Never use `eval()`, `Function()`, string `setTimeout`/`setInterval`
- Never use `document.write()`
- Don't expose API keys or sensitive data
- Don't modify native prototypes (Array.prototype, Object.prototype)
