# PHP Code Review Rules

## SQL Injection
- String concatenation or interpolation in SQL queries — use prepared statements with bound params (PDO)

## XSS — Escape All Output
- echo/print/`<?=` containing a variable without htmlspecialchars(, ENT_QUOTES|ENT_HTML5, 'UTF-8') or known-escaping function

## Type Juggling
- Loose comparisons (==, !=) especially with user input, hashes, passwords — use === and !==
- "0e123" == "0e456" is true (both evaluate to 0) — strict comparison required for security checks

## Null Coalescing
- `isset($x) ? $x : $default` — replace with `$x ?? $default` (PHP 7+)
- Use `$x ??= value` for assign-if-null pattern

## Session Fixation
- Authentication logic setting session variables without calling `session_regenerate_id(true)`

## CSRF Protection
- POST/PUT/DELETE handlers without CSRF token verification — generate and validate tokens

## File Upload Validation
- `move_uploaded_file` without MIME validation via `finfo_file()`, without size check, or using user-supplied filename directly

## Unserialize Vulnerabilities
- `unserialize()` on user input or external data — RCE vector; use `json_decode()` instead
- If required, use `allowed_classes` option to restrict

## Error Handling
- `display_errors = On` in production — leaks paths/stack traces
- Missing set_error_handler/set_exception_handler in bootstrap — use proper logging

## Autoloading
- Raw include/require of PHP class files — use Composer PSR-4 autoloading
