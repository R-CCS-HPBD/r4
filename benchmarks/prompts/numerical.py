"""
Numerical stability and precision prompts.
"""

NUMERICAL_PROMPTS = [
    {
        "id": "num_01_catastrophic_cancel",
        "category": "numerical",
        "language": "python",
        "prompt": (
            "Write a Python function `safe_sqrt_diff(x)` that computes sqrt(1 - x²) "
            "accurately even when x is close to 1, avoiding catastrophic cancellation. "
            "The naive approach sqrt(1 - x*x) loses precision when x ≈ 1. "
            "Use the identity: sqrt(1-x²) = sqrt((1-x)(1+x)) or similar stable formulation.\n"
            "The function must pass:\n"
            "assert abs(safe_sqrt_diff(0.5) - 0.8660254037844386) < 1e-10\n"
            "assert abs(safe_sqrt_diff(0.9999999) - 0.0004472134864257) < 1e-10\n"
            "assert safe_sqrt_diff(0.0) == 1.0\n"
            "assert safe_sqrt_diff(1.0) == 0.0\n"
            "Provide only the function definition."
        ),
        "test_code": """
import math

def test_safe_sqrt_normal():
    assert abs(safe_sqrt_diff(0.5) - 0.8660254037844386) < 1e-10

def test_safe_sqrt_near_one():
    # The naive sqrt(1-x*x) gives ~0 for x=0.9999999 due to cancellation
    result = safe_sqrt_diff(0.9999999)
    expected = math.sqrt(1 - 0.9999999**2)
    assert abs(result - expected) < 1e-10

def test_safe_sqrt_edge():
    assert safe_sqrt_diff(0.0) == 1.0
    assert safe_sqrt_diff(1.0) == 0.0
""",
        "timeout_sec": 5,
    },
    {
        "id": "num_02_gaussian_elim",
        "category": "numerical",
        "language": "python",
        "prompt": (
            "Write a Python function `solve_linear(A, b)` that solves the linear system "
            "Ax = b using Gaussian elimination with partial pivoting. "
            "A is an n×n matrix (list of lists), b is an n-element vector. "
            "Return the solution vector x. Handle singular matrices by returning None.\n"
            "The function must pass:\n"
            "A = [[2,1,-1], [-3,-1,2], [-2,1,2]]; b = [8, -11, -3]\n"
            "x = solve_linear(A, b); expected = [2, 3, -1]\n"
            "for i, v in enumerate(expected): assert abs(x[i] - v) < 1e-10\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_solve_3x3():
    A = [[2,1,-1], [-3,-1,2], [-2,1,2]]
    b = [8, -11, -3]
    x = solve_linear(A, b)
    expected = [2, 3, -1]
    for i, v in enumerate(expected):
        assert abs(x[i] - v) < 1e-10

def test_solve_identity():
    A = [[1,0,0],[0,1,0],[0,0,1]]
    b = [5, 6, 7]
    x = solve_linear(A, b)
    assert x == [5, 6, 7]

def test_solve_singular():
    A = [[1,2,3],[2,4,6],[3,6,9]]
    b = [1, 2, 3]
    assert solve_linear(A, b) is None
""",
        "timeout_sec": 10,
    },
    {
        "id": "num_03_log_sum_exp",
        "category": "numerical",
        "language": "python",
        "prompt": (
            "Write a Python function `log_sum_exp(values)` that computes "
            "log(sum(exp(v) for v in values)) in a numerically stable way. "
            "The naive approach can overflow or underflow for moderate values. "
            "Use the log-sum-exp trick: log(sum(exp(v))) = a + log(sum(exp(v-a))) "
            "where a = max(values).\n"
            "The function must pass:\n"
            "import math\n"
            "assert abs(log_sum_exp([1,2,3]) - 3.407605964444380) < 1e-10\n"
            "assert abs(log_sum_exp([1000, 1000]) - 1000.6931471805599453) < 1e-10\n"
            "assert abs(log_sum_exp([-1000, -1001]) - (-999.3132616875182)) < 1e-10\n"
            "Provide only the function definition."
        ),
        "test_code": """
import math

def test_log_sum_exp_basic():
    assert abs(log_sum_exp([1,2,3]) - 3.407605964444380) < 1e-10

def test_log_sum_exp_large():
    assert abs(log_sum_exp([1000, 1000]) - 1000.6931471805599453) < 1e-10

def test_log_sum_exp_negative():
    assert abs(log_sum_exp([-1000, -1001]) - (-999.3132616875182)) < 1e-10

def test_log_sum_exp_single():
    assert log_sum_exp([5]) == 5.0
""",
        "timeout_sec": 5,
    },
]
