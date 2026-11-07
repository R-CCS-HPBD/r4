"""
Profiling and performance analysis prompts.
"""

PROFILING_PROMPTS = [
    {
        "id": "pro_01_bottleneck_finder",
        "category": "profiling",
        "language": "python",
        "prompt": (
            "Analyze the following Python function. Identify the performance bottleneck(s), "
            "explain why they are slow, and provide an optimized version.\n\n"
            "Original code:\n"
            "```python\n"
            "def find_common_elements(lists):\n"
            "    '''Find elements common to all input lists.'''\n"
            "    if not lists:\n"
            "        return []\n"
            "    common = lists[0][:]\n"
            "    for lst in lists[1:]:\n"
            "        new_common = []\n"
            "        for elem in common:\n"
            "            if elem in lst:  # O(n) lookup in list!\n"
            "                new_common.append(elem)\n"
            "        common = new_common\n"
            "    return common\n"
            "```\n\n"
            "The function must pass:\n"
            "assert sorted(find_common_elements([[1,2,3,4,5], [3,4,5,6,7], [4,5,8,9]])) == [4,5]\n"
            "assert find_common_elements([]) == []\n"
            "Provide the optimized function (with brief explanation as comment)."
        ),
        "test_code": """
def test_common_basic():
    result = find_common_elements([[1,2,3,4,5], [3,4,5,6,7], [4,5,8,9]])
    assert sorted(result) == [4,5]

def test_common_empty():
    assert find_common_elements([]) == []
    assert find_common_elements([[1,2], [3,4]]) == []

def test_common_single():
    assert sorted(find_common_elements([[1,2,3]])) == [1,2,3]

def test_common_large():
    lists = [list(range(100)), list(range(50, 150)), list(range(25, 75))]
    assert sorted(find_common_elements(lists)) == list(range(50, 75))
""",
        "timeout_sec": 5,
    },
    {
        "id": "pro_02_memoize_wrapper",
        "category": "profiling",
        "language": "python",
        "prompt": (
            "Write a Python decorator `@memoize` that caches function results based on "
            "arguments for expensive recursive functions. The decorator should handle "
            "hashable arguments and use a dict for the cache.\n\n"
            "Use it on:\n"
            "```python\n"
            "@memoize\n"
            "def fib_memo(n):\n"
            "    if n <= 1: return n\n"
            "    return fib_memo(n-1) + fib_memo(n-2)\n"
            "```\n\n"
            "The decorated fib_memo(100) must compute quickly (no timeout). "
            "Also test that memoization caches results:\n"
            "assert fib_memo(30) == 832040\n"
            "assert fib_memo(100) == 354224848179261915075\n"
            "Provide the decorator definition and decorated function."
        ),
        "test_code": """
def test_memoize_correctness():
    assert fib_memo(0) == 0
    assert fib_memo(1) == 1
    assert fib_memo(10) == 55
    assert fib_memo(30) == 832040

def test_memoize_large():
    # Must compute quickly (memoized)
    assert fib_memo(100) == 354224848179261915075

def test_memoize_cache():
    import time
    start = time.time()
    _ = fib_memo(200)
    first_call = time.time() - start
    start = time.time()
    _ = fib_memo(200)
    second_call = time.time() - start
    # Cached call should be essentially instant
    assert second_call < 0.01
""",
        "timeout_sec": 5,
    },
]
