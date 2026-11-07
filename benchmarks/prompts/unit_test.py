"""
Unit-test based code generation prompts (HumanEval-style).

Each prompt provides a function signature + docstring + test cases.
The evaluation harness runs pytest against the generated code.
"""

UNIT_TEST_PROMPTS = [
    {
        "id": "ut_01_fibonacci",
        "category": "unit_test",
        "language": "python",
        "prompt": (
            "Write a Python function `fib(n)` that returns the n-th Fibonacci number. "
            "Use an efficient iterative approach (no recursion). "
            "The function must pass these tests:\n"
            "assert fib(0) == 0\n"
            "assert fib(1) == 1\n"
            "assert fib(10) == 55\n"
            "assert fib(20) == 6765\n"
            "assert fib(30) == 832040\n"
            "Provide only the function definition, no extra explanation."
        ),
        "test_code": """
def test_fib_small():
    assert fib(0) == 0
    assert fib(1) == 1
    assert fib(5) == 5

def test_fib_medium():
    assert fib(10) == 55
    assert fib(20) == 6765

def test_fib_large():
    assert fib(30) == 832040
    assert fib(35) == 9227465
""",
        "extraction_pattern": r"def fib\(.*",
        "timeout_sec": 5,
    },
    {
        "id": "ut_02_is_prime",
        "category": "unit_test",
        "language": "python",
        "prompt": (
            "Write a Python function `is_prime(n)` that returns True if n is a prime number, "
            "False otherwise. Handle edge cases: n <= 1 returns False. "
            "Use an efficient O(sqrt(n)) algorithm.\n"
            "The function must pass these tests:\n"
            "assert is_prime(2) == True\n"
            "assert is_prime(4) == False\n"
            "assert is_prime(17) == True\n"
            "assert is_prime(1) == False\n"
            "assert is_prime(97) == True\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_small_primes():
    assert is_prime(2) == True
    assert is_prime(3) == True
    assert is_prime(4) == False

def test_edge_cases():
    assert is_prime(0) == False
    assert is_prime(1) == False
    assert is_prime(-5) == False

def test_large_primes():
    assert is_prime(97) == True
    assert is_prime(100) == False
    assert is_prime(7919) == True
""",
        "extraction_pattern": r"def is_prime\(.*",
        "timeout_sec": 5,
    },
    {
        "id": "ut_03_merge_sorted",
        "category": "unit_test",
        "language": "python",
        "prompt": (
            "Write a Python function `merge_sorted(a, b)` that merges two sorted lists "
            "into a single sorted list. Do NOT use built-in sort — exploit the fact that "
            "inputs are already sorted to achieve O(n+m) time.\n"
            "The function must pass these tests:\n"
            "assert merge_sorted([1,3,5], [2,4,6]) == [1,2,3,4,5,6]\n"
            "assert merge_sorted([], [1,2]) == [1,2]\n"
            "assert merge_sorted([1,2], []) == [1,2]\n"
            "assert merge_sorted([1,1,2], [1,2,2]) == [1,1,1,2,2,2]\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_merge_basic():
    assert merge_sorted([1,3,5], [2,4,6]) == [1,2,3,4,5,6]

def test_merge_empty():
    assert merge_sorted([], [1,2]) == [1,2]
    assert merge_sorted([1,2], []) == [1,2]
    assert merge_sorted([], []) == []

def test_merge_duplicates():
    assert merge_sorted([1,1,2], [1,2,2]) == [1,1,1,2,2,2]
    assert merge_sorted([5,5,5], [5,5]) == [5,5,5,5,5]
""",
        "extraction_pattern": r"def merge_sorted\(.*",
        "timeout_sec": 5,
    },
    {
        "id": "ut_04_binary_search",
        "category": "unit_test",
        "language": "python",
        "prompt": (
            "Write a Python function `binary_search(arr, target)` that performs binary search "
            "on a sorted list and returns the index of target, or -1 if not found. "
            "Assume arr is sorted in ascending order.\n"
            "The function must pass these tests:\n"
            "assert binary_search([1,2,3,4,5], 3) == 2\n"
            "assert binary_search([1,2,3,4,5], 6) == -1\n"
            "assert binary_search([], 1) == -1\n"
            "assert binary_search([1], 1) == 0\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_binary_search_found():
    assert binary_search([1,2,3,4,5], 3) == 2
    assert binary_search([1,2,3,4,5], 1) == 0
    assert binary_search([1,2,3,4,5], 5) == 4

def test_binary_search_not_found():
    assert binary_search([1,2,3,4,5], 6) == -1
    assert binary_search([1,2,3,4,5], 0) == -1

def test_binary_search_edge():
    assert binary_search([], 1) == -1
    assert binary_search([1], 1) == 0
    assert binary_search([1], 2) == -1
""",
        "extraction_pattern": r"def binary_search\(.*",
        "timeout_sec": 5,
    },
    {
        "id": "ut_05_is_palindrome",
        "category": "unit_test",
        "language": "python",
        "prompt": (
            "Write a Python function `is_palindrome(s)` that returns True if string s is a "
            "palindrome, ignoring case, spaces, and punctuation. Only consider alphanumeric "
            "characters.\n"
            "The function must pass these tests:\n"
            "assert is_palindrome('RaceCar') == True\n"
            "assert is_palindrome('A man a plan a canal Panama') == True\n"
            "assert is_palindrome('Hello') == False\n"
            "assert is_palindrome('') == True\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_palindrome_simple():
    assert is_palindrome('racecar') == True
    assert is_palindrome('RaceCar') == True

def test_palindrome_with_spaces():
    assert is_palindrome('A man a plan a canal Panama') == True
    assert is_palindrome('Was it a car or a cat I saw') == True

def test_not_palindrome():
    assert is_palindrome('Hello') == False
    assert is_palindrome('Python') == False

def test_palindrome_edge():
    assert is_palindrome('') == True
    assert is_palindrome('a') == True
    assert is_palindrome('ab') == False
""",
        "extraction_pattern": r"def is_palindrome\(.*",
        "timeout_sec": 5,
    },
]
