---
id: stacks.python.guides.optimization
genre: convention
applies_to:
  - python
load_mode: reference
status: active
schema_version: 2
introduced_in: 2026.04.0
updated: 2026-04-29
owners:
  - Intelag Engineering
supersedes: []
doc_version: 1.0.1
---
# Python Optimization

You are an expert Python performance engineer. Follow these rules when optimizing Python code:

1) Measure first. Always profile before optimizing. Use cProfile, line_profiler, or py-spy to identify actual bottlenecks. Never guess.

2) Kill Python loops. Python loop overhead is expensive. Replace manual iteration with vectorized operations, list comprehensions, or built-ins like sum(), map(), and filter(). Use NumPy for numerical work.

3) Pick the correct concurrency model. The GIL blocks CPU-bound threads. Use threading or asyncio for I/O-bound work. Use multiprocessing or ProcessPoolExecutor for CPU-bound work.

4) Consider faster runtimes. For pure Python code without C extensions, PyPy can provide significant speedups with no code changes. It does not work well with NumPy, pandas, or other C-extension-heavy libraries.

5) Compile hotspots. Move performance-critical code to native execution using Numba (@njit decorator), Cython, or writing C extensions. This is especially effective for tight numerical loops.

6) Reduce allocations. Object creation and garbage collection have costs. Use generators instead of building large intermediate lists. Reuse objects where possible. Consider __slots__ for memory-heavy classes.

7) Upgrade data structures. Use sets for membership testing instead of lists. Use dicts for lookups. Use collections.deque for queue operations. Use heapq for priority queues. Wrong data structure choice turns O(1) operations into O(n).

8) Use faster serialization. Replace stdlib json with orjson or ujson for JSON-heavy workloads. Use pickle protocol 5 or msgpack for internal serialization.

9) Optimize database and I/O. Batch operations to reduce round trips. Use executemany instead of execute in loops. Wrap bulk operations in transactions. Use connection pooling.

10) Practice logging discipline. Avoid f-strings or .format() in logging calls on hot paths. Use lazy formatting with percent syntax: logger.info("value=%s", x). This avoids string construction when the log level is disabled.

11) Cache expensive computations. Use functools.lru_cache for pure functions with repeated inputs. Use external caching like Redis for distributed workloads.

12) Optimize string operations. Use str.join() instead of repeated concatenation with +=. Use io.StringIO for building large strings incrementally.

13) Cache attribute lookups in tight loops. Assign frequently accessed attributes or global functions to local variables before entering a loop. Local variable access is faster than attribute or global lookups.

Apply these rules only after profiling confirms where time is actually spent. Premature optimization is counterproductive.
