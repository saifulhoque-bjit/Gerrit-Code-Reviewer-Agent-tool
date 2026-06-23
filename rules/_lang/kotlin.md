# Kotlin Code Review Rules

Rules focused on real bugs, runtime failures, and production incidents. Not style.

---

## Null Safety
- Excessive `!!` operator — crashes on null; BAD: `user!!.name` GOOD: `user?.name ?: default`
- Chained `!!` — any null in chain crashes; BAD: `a!!.b!!.c` GOOD: `a?.b?.c ?: default`
- Unsafe cast `as` — ClassCastException; BAD: `x as Foo` GOOD: `x as? Foo` or `if (x is Foo)`
- Platform type from Java treated as non-null — NPE if Java returns null; BAD: `val s: String = javaObj.name` GOOD: `val s: String? = javaObj.name`
- `also` side-effect modifying the value — confusing mutation; BAD: `.also { it.add(x) }` GOOD: use `also` only for logging/observation
- Unnecessary `let` — `user?.let { it.name } ?: default` is just `user?.name ?: default`

## Coroutine Scope and Cancellation
- `GlobalScope.launch` — no cancellation, leaks; GOOD: `viewModelScope.launch`, `lifecycleScope.launch`, `rememberCoroutineScope().launch`
- Catching `Exception` without rethrowing `CancellationException` — prevents cancellation; BAD: `catch (e: Exception)` GOOD: `catch (e: CancellationException) { throw e } catch (e: Exception) { ... }`
- `runBlocking` inside a suspend function — blocks thread; GOOD: `withContext(Dispatchers.IO)`
- Wrong dispatcher — network/CPU on Main; BAD: `withContext(Dispatchers.Main) { fetch() }` GOOD: `withContext(Dispatchers.IO)`
- Coroutine launched in ViewModel constructor without viewModelScope — outlives ViewModel; BAD: `CoroutineScope(IO).launch { ... }` GOOD: `viewModelScope.launch { withContext(IO) { ... } }`
- Unhandled exception in `launch` — silent crash in supervisor scopes; GOOD: wrap in try/catch or use `CoroutineExceptionHandler`

## Flow Collection
- Collecting flow in `launch` without lifecycle awareness — runs in background; GOOD: `repeatOnLifecycle(Lifecycle.State.STARTED) { flow.collect { } }` or `collectAsStateWithLifecycle()`
- Missing terminal operator — cold flow never executes; BAD: `flow.map { }` GOOD: `flow.map { }.collect { }`
- Multiple collectors of shared flow — upstream runs per collector; GOOD: `flow.stateIn(scope, SharingStarted.WhileSubscribed(5000), initial)`
- `catch` operator only covers upstream — exceptions in `collect` still crash; GOOD: wrap `collect` body in try/catch
- `flowOn` after `collect` — compile error; GOOD: `.flowOn(Dispatchers.IO).collect { }`

## Sealed Class Exhaustiveness
- Missing branch in `when` on sealed class — silent omission; GOOD: list all branches explicitly, no `else` (compiler warns on new subclass)
- `else` branch on sealed class `when` — hides new subclasses; BAD: `else -> {}` GOOD: enumerate all subtypes

## Data Class Pitfalls
- Shallow `copy()` — mutable collections shared between original and copy; BAD: `original.copy()` with `MutableList` field; GOOD: use `List` in data classes or `copy(items = items.toMutableList())`
- Mutable fields in data class break `equals`/`hashCode` — modifying tags breaks HashSet lookup; GOOD: use `List` not `MutableList` in data class fields

## lateinit vs lazy
- `lateinit` accessed before init — `UninitializedPropertyAccessException`; Fragment binding: use `_binding` nullable + getter pattern, null in `onDestroyView`
- `lazy` with side effects in Fragment — holds reference preventing GC; GOOD: initialize in `onViewCreated`, null in `onDestroyView`
- `lateinit` with nullable type — nonsensical; use `lateinit var x: Foo` (non-null) or `var x: Foo? = null`

## Companion Object / Singleton
- Mutable state in companion without synchronization — race condition; GOOD: `AtomicReference`, `MutableStateFlow`, or `synchronized`
- Singleton without proper locking — BAD: `if (instance == null) instance = Foo()` GOOD: use `object` declaration or `@Volatile` + double-checked lock

## Android Lifecycle
- Handler postDelayed without cleanup — Activity may be destroyed; GOOD: remove callbacks in `onDestroy`, or use `lifecycleScope.launch { delay(); ... }`
- ViewModel holding Activity/View reference — massive leak; GOOD: use `Application` context or emit events via `Channel`/`SharedFlow`
- Adapter holding Activity reference — GOOD: accept lambda `onItemClick: (Item) -> Unit` instead
- `observeForever` in ViewModel — no lifecycle awareness, leaks; GOOD: use `StateFlow` + `viewModelScope.launch { collect { } }`
- LiveData `.value` from background thread — crash; GOOD: use `StateFlow` (thread-safe) or `postValue()`
- Flow collected in `onViewCreated` without `repeatOnLifecycle` — duplicate collectors on config change; GOOD: `viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED)`
- SharedPreferences on main thread — blocks UI; GOOD: use DataStore or read off main thread
