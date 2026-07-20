# Go Code Review Rules

## Unchecked Errors
- Ignoring returned `error` values (using `_` for error) — always handle or document why discarded
- `_ = someFunc()` explicit discard acceptable only with comment explaining why

## Goroutine Leaks
- Bare `go func()` without sync.WaitGroup, errgroup, or channel-based join — goroutine may outlive caller
- Use errgroup or context for structured concurrency

## Race Conditions
- Shared variable read/written from multiple goroutines without sync.Mutex, atomic, or channels
- sync.WaitGroup Add() must be called BEFORE go, not inside the goroutine
- Map is not goroutine-safe — use sync.Map or mutex-protected map

## Nil Pointer Dereference
- Direct method call on map return value without nil/ok check
- Using interface variable that was never assigned (var r io.Reader; r.Read(buf) panics)

## Defer in Loops
- `defer` inside a for loop — defers accumulate until function exit, not iteration end; extract helper function

## Context Propagation
- Creating http.NewRequest without context — use http.NewRequestWithContext
- context.Background()/TODO() inside business logic — breaks cancellation chain, propagate parent ctx

## Interface Satisfaction
- Type claimed to implement interface without compile-time assertion — use `var _ MyInterface = (*MyStruct)(nil)`

## String/Byte Conversion Performance
- `[]byte(s)` or `string(b)` inside loops/hot paths — each copies data; use strings.Builder or unsafe conversion

## HTTP Handler Safety
- HTTP handlers without panic recovery middleware — any panic crashes the server

## Database Connection Pool
- `db.Query`/`db.QueryRow` without `defer rows.Close()` — connection leaked
- `sql.Open` without `SetMaxOpenConns` — default unlimited connections exhausts pool
