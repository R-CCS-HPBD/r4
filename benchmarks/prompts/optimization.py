"""
Optimization and profiling prompts — given code, make it faster.
"""

OPTIMIZATION_PROMPTS = [
    {
        "id": "opt_01_slow_matrix_mul",
        "category": "optimization",
        "language": "python",
        "prompt": (
            "The following Python function multiplies two square matrices but is inefficient. "
            "Rewrite it to be more efficient. You may use any standard library features "
            "(e.g., optimize loop order for cache, use list comprehensions, or suggest "
            "numpy-based improvements).\n\n"
            "Original code:\n"
            "```python\n"
            "def matmul(a, b):\n"
            "    n = len(a)\n"
            "    result = [[0]*n for _ in range(n)]\n"
            "    for i in range(n):\n"
            "        for j in range(n):\n"
            "            for k in range(n):\n"
            "                result[i][j] += a[i][k] * b[k][j]\n"
            "    return result\n"
            "```\n\n"
            "Provide your optimized version. It must still pass:\n"
            "a = [[1,2],[3,4]]; b = [[5,6],[7,8]]\n"
            "assert matmul(a, b) == [[19,22],[43,50]]\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_matmul_2x2():
    a = [[1,2],[3,4]]
    b = [[5,6],[7,8]]
    expected = [[19,22],[43,50]]
    assert matmul(a, b) == expected

def test_matmul_identity():
    a = [[1,0],[0,1]]
    b = [[5,6],[7,8]]
    assert matmul(a, b) == b

def test_matmul_3x3():
    a = [[1,2,3],[4,5,6],[7,8,9]]
    b = [[9,8,7],[6,5,4],[3,2,1]]
    expected = [[30,24,18],[84,69,54],[138,114,90]]
    assert matmul(a, b) == expected
""",
        "timeout_sec": 30,
    },
    {
        "id": "opt_02_find_duplicates",
        "category": "optimization",
        "language": "python",
        "prompt": (
            "The following Python function finds duplicate elements in a list but is O(n²). "
            "Rewrite it to run in O(n) time using a set or dict.\n\n"
            "Original code:\n"
            "```python\n"
            "def find_duplicates(arr):\n"
            "    dupes = []\n"
            "    for i in range(len(arr)):\n"
            "        for j in range(i+1, len(arr)):\n"
            "            if arr[i] == arr[j] and arr[i] not in dupes:\n"
            "                dupes.append(arr[i])\n"
            "    return dupes\n"
            "```\n\n"
            "Provide your optimized version. It must pass:\n"
            "assert sorted(find_duplicates([1,2,3,2,1,4])) == [1,2]\n"
            "assert find_duplicates([1,2,3]) == []\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_find_dupes_basic():
    assert sorted(find_duplicates([1,2,3,2,1,4])) == [1,2]

def test_find_dupes_none():
    assert find_duplicates([1,2,3]) == []
    assert find_duplicates([]) == []

def test_find_dupes_all():
    assert sorted(find_duplicates([5,5,5,5])) == [5]
""",
        "timeout_sec": 5,
    },
    {
        "id": "opt_03_sum_even_squares",
        "category": "optimization",
        "language": "python",
        "prompt": (
            "Optimize the following Python code to compute the sum of squares of even "
            "numbers in a list. The current version creates intermediate lists unnecessarily.\n\n"
            "Original code:\n"
            "```python\n"
            "def sum_even_squares(numbers):\n"
            "    evens = [n for n in numbers if n % 2 == 0]\n"
            "    squares = [n*n for n in evens]\n"
            "    total = sum(squares)\n"
            "    return total\n"
            "```\n\n"
            "Rewrite to use a single generator expression (less memory, still readable). "
            "Must pass: assert sum_even_squares([1,2,3,4,5,6]) == 56\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_sum_even_squares():
    assert sum_even_squares([1,2,3,4,5,6]) == 56  # 4+16+36
    assert sum_even_squares([2,4,6]) == 56
    assert sum_even_squares([1,3,5]) == 0
    assert sum_even_squares([]) == 0
    assert sum_even_squares([0,0,0]) == 0
""",
        "timeout_sec": 5,
    },
]
