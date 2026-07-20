# AI-Specific Failure Modes

Check every finding against these patterns. These are systematic ways LLMs produce bad code.

## 1. Broad Error Swallowing
NEVER accept `catch (Exception)` that returns null/empty/silent. Catch only the specific error type you can recover from. If you can't recover, let it propagate.

**Pattern**: `try { ... } catch (Exception e) { return null; }`
**Fix**: Catch specific exceptions. Log and re-throw if unrecoverable.

## 2. Hardcoded Success Returns
NEVER accept functions that return `{"status": "ok"}` or canned data when the spec says they do real work. If you can't implement, fail explicitly.

**Pattern**: `return new Response("ok");` when the function should do real work
**Fix**: Implement the real logic or throw `UnsupportedOperationException`

## 3. Hallucinated APIs
BEFORE flagging an import or method call as wrong, verify it actually exists. Don't flag code for using an API that "shouldn't exist" if you can't confirm it doesn't.

**Pattern**: Flagging `StringUtils.isEmpty()` as "not a real method" when it is
**Fix**: Only flag if you can verify the API doesn't exist in the project's dependencies

## 4. Copy-From-Similar Bugs
When two functions look similar, check for off-by-one errors, wrong null semantics, and mismatched variable names. These bugs enter through copy-paste-modify.

**Pattern**: Two methods with similar structure but different edge case handling
**Fix**: Flag the inconsistency, suggest extracting shared logic

## 5. Dead Code
Flag unreachable branches, unused imports, unused variables, and large blocks of commented-out code.

**Pattern**: `if (false) { ... }` or variables declared but never read
**Fix**: Remove dead code

## 6. Defensive Guards for Impossible Cases
Don't add null checks for values whose type contract already excludes null. Trust the contract.

**Pattern**: `if (string != null)` when the parameter is `@NonNull String`
**Fix**: Remove unnecessary guards, trust type annotations

## 7. Premature Abstraction
No interface, base class, or factory with only one implementation. If there's only one concrete user today, inline it.

**Pattern**: `interface UserService` with only `UserServiceImpl`
**Fix**: Use the concrete class directly until a second implementation appears

## 8. Comment Pollution
Delete comments that paraphrase the line below them. Delete step-number scaffolding. Delete commented-out code.

**Pattern**: `// Increment counter\ncounter++;`
**Fix**: Delete the comment, the code is self-explanatory
