"""The house style bans comments inside a function body. This is what enforces it.

A comment heading a block is a function name that was never written down, and it
has no compiler, no test and no reviewer keeping it true.
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


class Counter(unittest.TestCase):
    """What the check counts, pinned on sources small enough to read."""

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


class Engine(unittest.TestCase):
    """iv-suggest carries no comment inside a function body."""

    def test_no_comment_sits_inside_a_function_body(self):
        with open(SCRIPT) as fh:
            found = body_comments(fh.read())
        self.assertEqual({}, found, _offenders(found))
