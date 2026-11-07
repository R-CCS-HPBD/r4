"""
Code synthesis prompts — real-world tasks (file I/O, data processing, API usage).
"""

CODE_SYNTHESIS_PROMPTS = [
    {
        "id": "syn_01_csv_parser",
        "category": "code_synthesis",
        "language": "python",
        "prompt": (
            "Write a Python function `parse_csv(text)` that parses CSV text and returns "
            "a list of lists (rows of columns). Handle quoted fields (double quotes) "
            'and escaped quotes within quoted fields (""). '
            "Do NOT use the csv module — implement the parser from scratch.\n"
            "The function must pass these tests:\n"
            'text = "a,b,c\\\\n1,2,3\\\\n4,5,6"\n'
            "assert parse_csv(text) == [['a','b','c'], ['1','2','3'], ['4','5','6']]\n"
            "text = 'name,age\\\\n\"Smith, John\",30\\\\nDoe,25'\n"
            "assert parse_csv(text) == [['name','age'], ['Smith, John','30'], ['Doe','25']]\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_csv_simple():
    text = "a,b,c\\n1,2,3\\n4,5,6"
    result = parse_csv(text)
    assert result == [['a','b','c'], ['1','2','3'], ['4','5','6']]

def test_csv_quoted():
    text = 'name,age\\n"Smith, John",30\\nDoe,25'
    result = parse_csv(text)
    assert result == [['name','age'], ['Smith, John','30'], ['Doe','25']]

def test_csv_empty():
    assert parse_csv("") == []
    assert parse_csv("a,b") == [['a','b']]
""",
        "timeout_sec": 10,
    },
    {
        "id": "syn_02_json_flattener",
        "category": "code_synthesis",
        "language": "python",
        "prompt": (
            "Write a Python function `flatten_json(obj, prefix='')` that flattens a nested "
            "JSON object (dict with nested dicts/lists) into a flat dict with dot-separated keys. "
            "Lists should use index notation [0], [1], etc.\n"
            "The function must pass:\n"
            "obj = {'a': 1, 'b': {'c': 2, 'd': [3, 4]}}\n"
            "assert flatten_json(obj) == {'a': 1, 'b.c': 2, 'b.d[0]': 3, 'b.d[1]': 4}\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_flatten_basic():
    obj = {'a': 1, 'b': {'c': 2, 'd': [3, 4]}}
    result = flatten_json(obj)
    assert result == {'a': 1, 'b.c': 2, 'b.d[0]': 3, 'b.d[1]': 4}

def test_flatten_deep():
    obj = {'x': {'y': {'z': 42}}}
    assert flatten_json(obj) == {'x.y.z': 42}

def test_flatten_empty():
    assert flatten_json({}) == {}
    assert flatten_json({'a': 1}) == {'a': 1}
""",
        "timeout_sec": 10,
    },
    {
        "id": "syn_03_url_parser",
        "category": "code_synthesis",
        "language": "python",
        "prompt": (
            "Write a Python function `parse_url(url)` that parses a URL string and returns "
            "a dict with keys: scheme, host, port, path, query (as dict), fragment. "
            "Handle default ports (http→80, https→443). Handle missing components.\n"
            "Do NOT use urllib.parse — implement from scratch.\n"
            "The function must pass:\n"
            'result = parse_url("https://example.com:8080/path?key=val&x=1#sec")\n'
            "assert result['scheme'] == 'https'\n"
            "assert result['host'] == 'example.com'\n"
            "assert result['port'] == 8080\n"
            "assert result['path'] == '/path'\n"
            "assert result['query'] == {'key': 'val', 'x': '1'}\n"
            "assert result['fragment'] == 'sec'\n"
            "Provide only the function definition."
        ),
        "test_code": """
def test_url_full():
    result = parse_url("https://example.com:8080/path?key=val&x=1#sec")
    assert result['scheme'] == 'https'
    assert result['host'] == 'example.com'
    assert result['port'] == 8080
    assert result['path'] == '/path'
    assert result['query'] == {'key': 'val', 'x': '1'}
    assert result['fragment'] == 'sec'

def test_url_default_port():
    result = parse_url("http://example.com/")
    assert result['scheme'] == 'http'
    assert result['host'] == 'example.com'
    assert result['port'] == 80

def test_url_minimal():
    result = parse_url("https://example.com")
    assert result['scheme'] == 'https'
    assert result['host'] == 'example.com'
    assert result['port'] == 443
    assert result['path'] == '/'
    assert result['query'] == {}
    assert result['fragment'] is None
""",
        "timeout_sec": 10,
    },
]
