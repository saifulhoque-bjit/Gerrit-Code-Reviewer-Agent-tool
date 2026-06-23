# C/C++ Code Review Rules

## Buffer Overflows
- Unsafe string functions: strcpy/strcat/sprintf/gets — use strncpy/snprintf/fgets with explicit size
- Off-by-one in buffer operations (strncpy may not null-terminate)
- Array index out-of-bounds in loops (<= vs < with array size)

## Memory Leaks
- malloc/calloc without free on all code paths (early returns, error branches)
- new[]/delete mismatch (must use delete[] for arrays)
- Missing cleanup on exception paths — prefer RAII (unique_ptr, smart pointers, containers)
- Raw owning pointers in classes without destructor/copy/move — use Rule of Zero (prefer unique_ptr/vector)

## Use-After-Free
- Pointer used after free/delete — set to nullptr after free
- Returning pointer/reference to local stack variables (dangling pointer)
- shared_ptr::get() raw pointer escaping before shared_ptr is destroyed

## Null Pointer Dereference
- Unchecked malloc/calloc return (NULL check required)
- Unchecked pointer before dereference from function returns or lookups
- Unchecked dynamic_cast result (returns nullptr on failure)

## Integer Overflow
- Signed integer overflow is undefined behavior — check before arithmetic or use unsigned
- Overflow in size calculations: count * sizeof() — validate before multiplication
- Unsigned wraparound in comparisons (len-1 when len==0 wraps to UINT_MAX)

## Format Strings
- User-controlled format string in printf/fprintf/syslog — always use "%s" wrapper
- Wrong format specifier: %d for size_t (use %zu), %d for long long (use %lld)

## Const Correctness
- Parameters not modified should be const (clarifies intent, prevents accidental mutation)
- Casting away const with const_cast is UB if original object was const

## Smart Pointer Misuse
- Prefer unique_ptr over shared_ptr when single owner suffices
- Creating shared_ptr from same raw pointer twice causes double-delete
- Circular shared_ptr references leak memory — use weak_ptr to break cycles

## Thread Safety
- Data races on shared mutable state — use atomic or mutex
- Manual lock/unlock without lock_guard — exception-unsafe, use RAII locking
- Inconsistent lock ordering across threads causes deadlock — always acquire in same order
- Double-checked locking without std::atomic — use atomic with acquire/release

## Undefined Behavior
- Uninitialized variables used before assignment
- Strict aliasing violations (accessing float through int*) — use memcpy instead
- Multiple modifications of same variable in one expression (i++ + i++)
- Throwing from destructor causes std::terminate — mark noexcept, catch exceptions

## STL Iterator Invalidation
- Erasing while iterating without using returned iterator (it = v.erase(it))
- Using iterators after push_back/insert that may reallocate (reserve first)
- unordered_map erase in loop without using erase() return value

## Polymorphic Classes
- Missing virtual destructor in base class with virtual methods — causes UB on delete via base pointer
- Self-assignment check missing in operator= (if this == &other guard)
