# Swift-Specific Review Rules

## Force Unwrapping & Optionals
- Force unwrap (`!`) on values already non-optional — unnecessary and misleading
- Force unwrap on data that can fail at runtime (dictionary subscripts, `URL(string:)`, `array.first`, JSON decode results)
  BAD: `let value = json["key"]!` / GOOD: `let value = json["key"] ?? ""`
- Force cast (`as!`) — crashes if cast fails
  BAD: `as! CustomCell` / GOOD: `as? CustomCell` with `guard let`
- Implicitly unwrapped optionals (`var x: Type!`) outside of `@IBOutlet` — hides nil bugs
  BAD: `var user: User!` / GOOD: `var user: User?`

## Retain Cycles
- Closure capturing `self` strongly in stored property or escaping closure — needs `[weak self]`
  BAD: `handler = { self.doWork() }` / GOOD: `handler = { [weak self] in self?.doWork() }`
- Delegate property not declared `weak` — protocol must be `: AnyObject`
  BAD: `var delegate: MyDelegate?` / GOOD: `weak var delegate: MyDelegate?`
- `Timer` / `NotificationCenter` target-action retains observer — use closure-based API with `[weak self]`
- `[weak self]` + `guard let self` then passed into another escaping closure — re-captures strong self
- Combine `sink`/`assign` closure retaining `self` — use `[weak self]` or `assign(to: &$prop)`
- Combine `assign(to: \.prop, on: self)` retains self — use `assign(to: &$prop)` instead
- Combine subscription return value discarded — must `.store(in: &cancellables)`

## Threading / MainActor
- UI updates not on main thread — mark function `@MainActor` or use `await MainActor.run { }`
  BAD: `Task.detached { self.label.text = data }` / GOOD: `Task { @MainActor in self.label.text = data }`
- `@MainActor` class accessed from non-isolated context without `await` — data race in Swift 6
- Non-`Sendable` values passed across actor boundaries — use actor or `@Sendable` value types
- `DispatchQueue.main.sync` on main thread — deadlock. Use `.async` or `await MainActor.run`
- `Task.detached { }` when MainActor context is needed — use `Task { }` to inherit actor
- `withCheckedContinuation` resume called more than once or not at all — crash or deadlock

## SwiftUI
- `@ObservedObject` for view-owned model — object lost on re-init. Use `@StateObject`
  BAD: `@ObservedObject var vm = ViewModel()` / GOOD: `@StateObject private var vm = ViewModel()`
- `@StateObject` initialized with external value — subsequent parent updates won't propagate
  BAD: `@StateObject var vm: ViewModel` passed from parent / GOOD: use `@ObservedObject`
- `@State` for reference types — mutations won't trigger view redraw. Use `@StateObject` or `@ObservedObject`
- `@EnvironmentObject` accessed without being injected in ancestor tree — runtime crash
- `@Binding` to a local/temporary value — must point to `@State` in parent

## Error Handling
- `try?` silently swallowing errors when error matters for debugging — use `do/try/catch`
  BAD: `let r = try? decoder.decode(...)` / GOOD: `do { try ... } catch { logger.error(error) }`
- Empty `catch` block — at minimum log the error
  BAD: `catch { }` / GOOD: `catch { logger.warning(error) }`
- `try!` crashes on failure — only safe when failure is provably impossible
- `catch` without binding the error — `catch { }` without `as Error` makes debugging impossible

## Security
- Hardcoded secrets / API keys in source — use Keychain or `.xcconfig` excluded from git
  BAD: `let apiKey = "sk-123..."` / GOOD: Keychain or environment config
- `UserDefaults` for sensitive data — it's a plain plist. Use Keychain Services
- Logging sensitive data (`print`, `os_log`) with tokens or PII — strip in release builds
- Unvalidated deep links / URL schemes — validate all URL parameters before use
- Insecure deserialization from untrusted JSON — set limits on `JSONDecoder` (iOS 15+)

## Crash-Prone Patterns
- Array index out of bounds without bounds check
  BAD: `items[indexPath.row]` / GOOD: `guard indexPath.row < items.count else { return }`
- `fatalError()` / `preconditionFailure()` reachable from user input or network data
- Missing `@unknown default` in switch on framework enums — new cases can be added by Apple
- String index from one string used on another — `String.Index` is not an integer
