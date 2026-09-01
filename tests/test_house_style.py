"""The house style bans body comments and long docstrings. This is what enforces it.

A comment heading a block is a function name that was never written down, and a
docstring that grows into prose is the same rot one indent further in. Neither
has a compiler, a test or a reviewer keeping it true.
"""

import ast
import io
import tokenize
import unittest

from support import SCRIPT


def _header_end_line(function, tokens):
    """Line of the colon closing a def header, after which the body starts."""
    depth = 0
    for token in tokens:
        if token.start[0] < function.lineno or token.type != tokenize.OP:
            continue
        if token.string in "([{":
            depth += 1
        elif token.string in ")]}":
            depth -= 1
        elif token.string == ":" and depth == 0:
            return token.start[0]
    raise AssertionError("no header colon for %s" % function.name)


def _named_functions(tree):
    """Every function in the tree, paired with its dotted name."""
    found = []

    def descend(node, prefix):
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef)):
                descend(child, prefix)
                continue
            name = "%s.%s" % (prefix, child.name) if prefix else child.name
            if not isinstance(child, ast.ClassDef):
                found.append((name, child))
            descend(child, name)

    descend(tree, "")
    return found


def body_comments(source):
    """Comment lines inside a function body, keyed by the innermost function."""
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    bodies = [(_header_end_line(function, tokens) + 1, function.end_lineno, name)
              for name, function in _named_functions(ast.parse(source))]
    found = {}
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        enclosing = [body for body in bodies if body[0] <= token.start[0] <= body[1]]
        if enclosing:
            found.setdefault(max(enclosing)[2], []).append(token.start[0])
    return found


def _offenders(found):
    """The failure message: which function, and which lines to go and look at."""
    listed = "\n".join(
        "  %s: line%s %s" % (name, "" if len(lines) == 1 else "s",
                             ", ".join(str(n) for n in lines))
        for name, lines in sorted(found.items()))
    return ("%d comment line(s) inside a function body, across %d function(s).\n"
            "Name the block or the value instead of heading it with a comment:\n%s"
            % (sum(len(lines) for lines in found.values()), len(found), listed))


def docstring_lines(source):
    """Lines each function's docstring occupies, keyed by the innermost function."""
    found = {}
    for name, function in _named_functions(ast.parse(source)):
        if ast.get_docstring(function, clean=False) is None:
            continue
        docstring = function.body[0]
        found[name] = docstring.end_lineno - docstring.lineno + 1
    return found


MOST_DOCSTRING_LINES = 2


def _too_long(counted):
    """Only the functions spending more docstring lines than the house style allows."""
    return {name: lines for name, lines in counted.items()
            if lines > MOST_DOCSTRING_LINES}


def _prolix(over):
    """The failure message: which function, and how many lines it spends saying it."""
    listed = "\n".join("  %s: %d lines" % (name, lines)
                       for name, lines in sorted(over.items(),
                                                 key=lambda kv: (-kv[1], kv[0])))
    return ("%d function(s) spend more than %d line(s) on a docstring, %d lines "
            "in all.\nSay what it does and what it returns, and carry the rest in "
            "a name or in docs/:\n%s"
            % (len(over), MOST_DOCSTRING_LINES, sum(over.values()), listed))


class Counter(unittest.TestCase):
    """What the checks count, pinned on sources small enough to read."""

    def test_a_comment_heading_a_block_is_counted(self):
        self.assertEqual({"f": [2]}, body_comments("def f():\n    # heading\n    return 1\n"))

    def test_a_hash_inside_a_docstring_is_not_a_comment(self):
        self.assertEqual({}, body_comments('def f():\n    """doc # not one"""\n    return 1\n'))

    def test_a_module_level_comment_is_left_alone(self):
        self.assertEqual({}, body_comments("# why this module exists\nX = 1\n"))

    def test_a_comment_in_a_multi_line_signature_is_not_in_the_body(self):
        self.assertEqual({}, body_comments("def f(\n    a,  # unit\n):\n    return a\n"))

    def test_a_nested_function_owns_its_own_comments(self):
        self.assertEqual({"outer.inner": [3]}, body_comments(
            "def outer():\n    def inner():\n        # why\n        return 1\n"
            "    return inner\n"))

    def test_a_method_is_named_by_its_class(self):
        self.assertEqual({"C.m": [3]}, body_comments(
            "class C:\n    def m(self):\n        # why\n        return 1\n"))

    def test_a_trailing_comment_counts_like_any_other(self):
        self.assertEqual({"f": [2]}, body_comments("def f():\n    return 1  # why\n"))

    def test_a_one_line_docstring_costs_one_line(self):
        self.assertEqual({"f": 1}, docstring_lines('def f():\n    """One."""\n    return 1\n'))

    def test_a_function_with_no_docstring_is_not_counted_at_all(self):
        self.assertEqual({}, docstring_lines("def f():\n    return 1\n"))

    def test_the_closing_quotes_cost_a_line_of_their_own(self):
        self.assertEqual({"f": 3}, docstring_lines(
            'def f():\n    """One.\n    Two.\n    """\n    return 1\n'))

    def test_a_two_line_docstring_is_within_the_limit(self):
        counted = docstring_lines('def f():\n    """One.\n    Two."""\n    return 1\n')
        self.assertEqual({"f": 2}, counted)
        self.assertEqual({}, _too_long(counted))

    def test_a_blank_separator_line_is_counted_like_any_other(self):
        self.assertEqual({"f": 5}, docstring_lines(
            'def f():\n    """One.\n\n    Prose.\n    More prose.\n    """\n    return 1\n'))

    def test_a_module_docstring_is_left_alone(self):
        self.assertEqual({}, docstring_lines('"""Module.\n\nProse.\n"""\nX = 1\n'))

    def test_a_class_docstring_is_left_alone(self):
        self.assertEqual({}, docstring_lines('class C:\n    """One.\n\n    Prose.\n    """\n'))

    def test_a_method_is_named_by_its_class_here_too(self):
        self.assertEqual({"C.m": 1}, docstring_lines(
            'class C:\n    def m(self):\n        """One."""\n'))

    def test_a_nested_function_owns_its_own_docstring(self):
        self.assertEqual({"outer": 1, "outer.inner": 1}, docstring_lines(
            'def outer():\n    """One."""\n    def inner():\n        """Two."""\n'
            "    return inner\n"))

    def test_only_the_functions_over_the_limit_are_reported(self):
        counted = {"short": 1, "caveat": 2, "prose": 6, "essay": 13}
        self.assertEqual({"prose": 6, "essay": 13}, _too_long(counted))

    def test_the_message_names_each_offender_and_its_line_count(self):
        message = _prolix({"prose": 6, "essay": 13})
        self.assertIn("essay: 13 lines", message)
        self.assertIn("prose: 6 lines", message)
        self.assertIn("2 function(s)", message)
        self.assertIn("19 lines in all", message)

    def test_the_worst_offender_is_named_first(self):
        message = _prolix({"prose": 6, "essay": 13})
        self.assertLess(message.index("essay"), message.index("prose"))


class Engine(unittest.TestCase):
    """iv-suggest carries no body comment and no docstring longer than two lines."""

    def test_no_comment_sits_inside_a_function_body(self):
        with open(SCRIPT) as fh:
            found = body_comments(fh.read())
        self.assertEqual({}, found, _offenders(found))

    def test_no_docstring_grows_past_two_lines(self):
        with open(SCRIPT) as fh:
            over = _too_long(docstring_lines(fh.read()))
        self.assertEqual({}, over, _prolix(over))
