# Default Review Checklist

## Correctness
- Is the logic correct? Are boundary conditions handled?
- Are exceptions handled properly (not swallowed)?
- Is thread safety considered in concurrent scenarios?
- Are there null/empty checks where data could be missing?

## Security
- Are there SQL injection, XSS, or other injection vulnerabilities?
- Is sensitive information handled correctly (not logged, not exposed)?
- Is input validated and sanitized before use?
- Are authentication and authorization checks complete?

## Performance
- Are there N+1 queries or unnecessary loops?
- Are resources properly closed (connections, streams, file handles)?
- Are database queries paginated (no unbounded fetches)?
- Are there O(n²) or worse algorithms where better options exist?

## Maintainability
- Is the code clear and easy to understand?
- Do names accurately express intent?
- Does it follow the project's existing patterns and conventions?
- Is there duplicate code that should be extracted?

## Error Handling
- Are error messages user-friendly (no stack traces to users)?
- Do network calls have timeout and retry handling?
- Are resources cleaned up in finally blocks or try-with-resources?
- Are failures logged with enough context to debug?
