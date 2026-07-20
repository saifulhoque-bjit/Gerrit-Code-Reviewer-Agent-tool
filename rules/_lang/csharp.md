# C#-Specific Review Rules

Rules focused on real bugs, runtime failures, and production incidents. Not style.

---

## Null References

1. **Dereference possibly-null ref** — `user.Name` where user can be null
   BAD: `user.Name.Trim()` → GOOD: `user?.Name?.Trim() ?? ""`
2. **Missing null after FirstOrDefault/SingleOrDefault** — result can be null
   BAD: `.FirstOrDefault(...).Name` → GOOD: `?? throw new NotFoundException(...)`
3. **Null-conditional result used without fallback**
   BAD: `collection?.ToList()` iterated directly → GOOD: `?? new List<T>()`
4. **`as` cast dereferenced without check**
   BAD: `(sender as Button).IsEnabled = false` → GOOD: `if (sender is Button b) b.IsEnabled = false;`
5. **Null-forgiving `!` suppressing real warning**
   BAD: `user!.Name` → GOOD: `user ?? throw new NotFoundException(...)`

---

## IDisposable / Resources

6. **IDisposable not in `using`** — streams, readers, DB connections leak
   BAD: `var s = new FileStream(...)` → GOOD: `using var s = new FileStream(...);`
7. **Disposing injected dependency** — DI container owns it, double-dispose
   BAD: `_context.Dispose()` in method → GOOD: use `IDbContextFactory<>`
8. **HttpClient per-call in `using`** — socket exhaustion
   BAD: `using var c = new HttpClient()` → GOOD: inject `IHttpClientFactory`

---

## Async/Await

9. **`async void`** — exceptions crash process or vanish
   BAD: `async void Handle()` → GOOD: `async Task HandleAsync()`
10. **Fire-and-forget loses exceptions**
    BAD: `_ = ProcessAsync()` → GOOD: wrap with try/catch in `Task.Run`
11. **Blocking on async** — `.Result` / `.GetAwaiter().GetResult()` deadlocks
    BAD: `GetDataAsync().Result` → GOOD: make chain async
12. **`await` inside `lock`** — deadlock
    BAD: `lock(o) { await X(); }` → GOOD: `await _sem.WaitAsync(); try { await X(); } finally { _sem.Release(); }`
13. **No CancellationToken** on long-running async calls
14. **Missing ConfigureAwait(false)** in library code (not needed in ASP.NET Core)

---

## Thread Safety

15. **Dictionary from multiple threads** — race condition
    BAD: `Dictionary<K,V>` → GOOD: `ConcurrentDictionary<K,V>`
16. **Check-then-act race**
    BAD: `if (!d.ContainsKey(k)) d[k]=...` → GOOD: `d.GetOrAdd(k, ...)`
17. **Lock on `this`** — external code can deadlock
    BAD: `lock(this)` → GOOD: `private readonly object _lock = new();`
18. **Static mutable state without sync**
    BAD: `_count++` → GOOD: `Interlocked.Increment(ref _count)`

---

## LINQ / Deferred Execution

19. **Multiple enumeration** — DB query fires per enumeration
    BAD: `if (q.Any()) { q.Count(); foreach ... }` → GOOD: `var list = q.ToList();`
20. **Closure over loop variable** — all see final value
    BAD: `for(i...) actions.Add(()=>i)` → GOOD: `var c=i; actions.Add(()=>c)`

---

## Blazor

21. **Using injected services after disposal** — timer accessing disposed HttpClient
    → use CancellationTokenSource, cancel in `DisposeAsync`
22. **StateHasChanged from background thread** — crash in Blazor Server
    BAD: `StateHasChanged()` → GOOD: `await InvokeAsync(StateHasChanged)`
23. **JS interop module not disposed** — IJSObjectReference leaked
    → implement `IAsyncDisposable`, call `_module.DisposeAsync()`
24. **Infinite re-render loop** — modifying `[Parameter]` in `OnParametersSet`
    BAD: `Items.Sort()` → GOOD: derive local field, guard with change check

---

## EF Core

25. **N+1 query (missing Include)** — N extra queries per nav property
    BAD: `Orders.ToListAsync()` then `order.Customer.Name` → GOOD: `.Include(o=>o.Customer)`
26. **Tracking when read-only** — unnecessary memory/CPU
    → add `.AsNoTracking()`
27. **DbContext as singleton** — not thread-safe → register Scoped or use factory
28. **SaveChangesAsync in loop** — one UPDATE per entity
    → batch: loop sets state, single SaveChangesAsync after
29. **AsNoTracking + SaveChanges** — silent no-op, entity not tracked
    → Attach+mark modified, or use tracking query
30. **Multi-step save without transaction** — partial commit on failure
    → `await using var tx = await context.Database.BeginTransactionAsync();`
