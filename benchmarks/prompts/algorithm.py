"""
Algorithmic problem prompts (DP, graph, FFT, sorting, etc.).
"""

ALGORITHM_PROMPTS = [
    {
        "id": "alg_01_longest_common_subsequence",
        "category": "algorithm",
        "language": "python",
        "prompt": (
            "Write a Python function `lcs(s1, s2)` that computes the length of the "
            "longest common subsequence of two strings using dynamic programming. "
            "The function should run in O(n*m) time and O(n*m) or O(min(n,m)) space.\n"
            "The function must pass these tests:\n"
            "assert lcs('abcde', 'ace') == 3\n"
            "assert lcs('abc', 'abc') == 3\n"
            "assert lcs('abc', 'def') == 0\n"
            "assert lcs('', 'abc') == 0\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_lcs_basic():
    assert lcs('abcde', 'ace') == 3
    assert lcs('abc', 'abc') == 3
    assert lcs('abc', 'def') == 0

def test_lcs_empty():
    assert lcs('', 'abc') == 0
    assert lcs('abc', '') == 0
    assert lcs('', '') == 0

def test_lcs_repeated():
    assert lcs('aaaa', 'aa') == 2
    assert lcs('AGGTAB', 'GXTXAYB') == 4
""",
        "timeout_sec": 10,
    },
    {
        "id": "alg_02_coin_change",
        "category": "algorithm",
        "language": "python",
        "prompt": (
            "Write a Python function `coin_change(coins, amount)` that returns the "
            "minimum number of coins needed to make up the given amount. "
            "If the amount cannot be made up by any combination, return -1. "
            "Use dynamic programming. Assume infinite supply of each coin.\n"
            "The function must pass these tests:\n"
            "assert coin_change([1,2,5], 11) == 3  # 5+5+1\n"
            "assert coin_change([2], 3) == -1\n"
            "assert coin_change([1], 0) == 0\n"
            "assert coin_change([1,5,10,25], 30) == 2  # 25+5\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_coin_change_basic():
    assert coin_change([1,2,5], 11) == 3

def test_coin_change_impossible():
    assert coin_change([2], 3) == -1

def test_coin_change_zero():
    assert coin_change([1], 0) == 0

def test_coin_change_large():
    assert coin_change([1,5,10,25], 30) == 2
    assert coin_change([186,419,83,408], 6249) == 20
""",
        "timeout_sec": 30,
    },
    {
        "id": "alg_03_dijkstra",
        "category": "algorithm",
        "language": "python",
        "prompt": (
            "Write a Python function `dijkstra(graph, start)` that implements Dijkstra's "
            "shortest path algorithm. The graph is represented as an adjacency list: "
            "a dict where graph[node] = [(neighbor, weight), ...]. "
            "Return a dict mapping each node to its shortest distance from start "
            "(use float('inf') for unreachable nodes).\n"
            "The function must pass:\n"
            "g = {0:[(1,4),(2,1)], 1:[(3,1)], 2:[(1,2),(3,5)], 3:[]}\n"
            "assert dijkstra(g, 0) == {0:0, 1:3, 2:1, 3:4}\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_dijkstra_basic():
    g = {0:[(1,4),(2,1)], 1:[(3,1)], 2:[(1,2),(3,5)], 3:[]}
    assert dijkstra(g, 0) == {0:0, 1:3, 2:1, 3:4}

def test_dijkstra_disconnected():
    g = {0:[(1,1)], 1:[], 2:[]}
    result = dijkstra(g, 0)
    assert result[0] == 0
    assert result[1] == 1
    assert result[2] == float('inf')

def test_dijkstra_single():
    g = {0:[]}
    assert dijkstra(g, 0) == {0:0}
""",
        "timeout_sec": 10,
    },
    {
        "id": "alg_04_quicksort",
        "category": "algorithm",
        "language": "python",
        "prompt": (
            "Write a Python function `quicksort(arr)` that sorts a list of integers "
            "in-place using the quicksort algorithm with Lomuto partition scheme. "
            "The function should sort the array in ascending order and return it.\n"
            "The function must pass these tests:\n"
            "assert quicksort([3,1,4,1,5,9,2,6]) == [1,1,2,3,4,5,6,9]\n"
            "assert quicksort([]) == []\n"
            "assert quicksort([1]) == [1]\n"
            "assert quicksort([5,4,3,2,1]) == [1,2,3,4,5]\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_quicksort_basic():
    assert quicksort([3,1,4,1,5,9,2,6]) == [1,1,2,3,4,5,6,9]

def test_quicksort_edge():
    assert quicksort([]) == []
    assert quicksort([1]) == [1]
    assert quicksort([1,1,1]) == [1,1,1]

def test_quicksort_reverse():
    assert quicksort([5,4,3,2,1]) == [1,2,3,4,5]
    assert quicksort([9,7,5,3,1]) == [1,3,5,7,9]
""",
        "timeout_sec": 10,
    },
    {
        "id": "alg_05_knapsack01",
        "category": "algorithm",
        "language": "python",
        "prompt": (
            "Write a Python function `knapsack_01(weights, values, capacity)` that solves "
            "the 0/1 knapsack problem using dynamic programming. "
            "Return the maximum total value achievable without exceeding capacity.\n"
            "The function must pass these tests:\n"
            "assert knapsack_01([2,3,4,5], [3,4,5,6], 5) == 7\n"
            "assert knapsack_01([1,2,3], [10,15,40], 6) == 55\n"
            "assert knapsack_01([10,20,30], [60,100,120], 50) == 220\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_knapsack_basic():
    assert knapsack_01([2,3,4,5], [3,4,5,6], 5) == 7

def test_knapsack_all_fit():
    assert knapsack_01([1,2,3], [10,15,40], 6) == 55

def test_knapsack_classic():
    assert knapsack_01([10,20,30], [60,100,120], 50) == 220

def test_knapsack_empty():
    assert knapsack_01([], [], 10) == 0
    assert knapsack_01([5], [10], 3) == 0
""",
        "timeout_sec": 30,
    },
]
