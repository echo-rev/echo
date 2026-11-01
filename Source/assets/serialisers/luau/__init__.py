'''
Lets scripts written in modern Studio (2022-2026 Luau) run on the 2021 engine.

`downgrade(source)` rewrites syntax the 2021 Luau parser doesn't know into code it does,
and puts small stand-ins for newer libraries (`task`, `table.clone`, ...) at the top of
scripts that use them. It runs in memory while RFD serves a place; files on disk are
never changed.

Every rewrite keeps the script's line numbers, so runtime errors still point at the
creator's own lines.

Handled: string interpolation, `//` and `//=`, compound assignment, if-expressions,
generalised iteration (`for k, v in t`), type annotations and type aliases, `::` casts,
generic function parameters, function attributes (`@native`), binary and `_`-separated
number literals.
If a script can't be parsed, it is returned unchanged.
'''

from __future__ import annotations

import dataclasses
import re

KEYWORDS = {
    'and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for', 'function', 'if', 'in',
    'local', 'nil', 'not', 'or', 'repeat', 'return', 'then', 'true', 'until', 'while',
}
OPS = sorted([
    '...', '..=', '//=', '..', '==', '~=', '<=', '>=', '->', '::', '+=', '-=', '*=', '/=', '%=',
    '^=', '//', '+', '-', '*', '/', '%', '^', '#', '&', '|', '?', '<', '>', '=', '(', ')', '{',
    '}', '[', ']', ';', ':', ',', '.', '@',
], key=len, reverse=True)
COMPOUND = {'+=': '+', '-=': '-', '*=': '*', '/=': '/', '%=': '%', '^=': '^', '..=': '..', '//=': '//'}
BINARY_PRIORITY = {  # (left, right) like the Lua reference parser
    'or': (1, 1), 'and': (2, 2),
    '<': (3, 3), '>': (3, 3), '<=': (3, 3), '>=': (3, 3), '~=': (3, 3), '==': (3, 3),
    '..': (5, 4),
    '+': (6, 6), '-': (6, 6),
    '*': (7, 7), '/': (7, 7), '//': (7, 7), '%': (7, 7),
    '^': (10, 9),
}
UNARY_PRIORITY = 8


class LuauSyntaxError(Exception):
    pass


@dataclasses.dataclass
class Token:
    kind: str  # name, keyword, number, string, interp, op, eof
    value: str
    start: int
    end: int
    # interp only: [('lit', start, end) | ('expr', tokens, start, end)]
    parts: list | None = None


class Lexer:
    def __init__(self, src: str) -> None:
        self.src = src

    def skip_space(self, i: int) -> int:
        s = self.src
        while i < len(s):
            c = s[i]
            if c in ' \t\r\n\f\v':
                i += 1
            elif s.startswith('--', i):
                m = re.compile(r'--\[(=*)\[').match(s, i)
                if m:
                    close = ']' + m.group(1) + ']'
                    j = s.find(close, m.end())
                    if j < 0:
                        raise LuauSyntaxError('unfinished long comment')
                    i = j + len(close)
                else:
                    j = s.find('\n', i)
                    i = len(s) if j < 0 else j
            else:
                break
        return i

    def tokens(self, i: int = 0, stop_at_brace: bool = False) -> tuple[list[Token], int]:
        '''Tokenises from `i`. With `stop_at_brace`, stops at an unmatched `}` (end of an interpolation hole).'''
        s = self.src
        out: list[Token] = []
        depth = 0
        while True:
            i = self.skip_space(i)
            if i >= len(s):
                if stop_at_brace:
                    raise LuauSyntaxError('unfinished interpolated string')
                out.append(Token('eof', '', i, i))
                return out, i
            c = s[i]
            if stop_at_brace and c == '}' and depth == 0:
                out.append(Token('eof', '', i, i))
                return out, i
            if c.isalpha() or c == '_':
                m = re.compile(r'[A-Za-z_][A-Za-z0-9_]*').match(s, i)
                word = m.group(0)
                out.append(Token('keyword' if word in KEYWORDS else 'name', word, i, m.end()))
                i = m.end()
            elif c.isdigit() or (c == '.' and i + 1 < len(s) and s[i + 1].isdigit()):
                m = re.compile(
                    r'0[xX][0-9A-Fa-f_]+|0[bB][01_]+|(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9][0-9_]*)(?:[eE][+-]?[0-9_]+)?'
                ).match(s, i)
                out.append(Token('number', m.group(0), i, m.end()))
                i = m.end()
            elif c in '"\'':
                j = i + 1
                while j < len(s) and s[j] != c:
                    if s[j] == '\\':
                        j += 1
                    elif s[j] == '\n':
                        raise LuauSyntaxError('unfinished string')
                    j += 1
                if j >= len(s):
                    raise LuauSyntaxError('unfinished string')
                out.append(Token('string', s[i:j + 1], i, j + 1))
                i = j + 1
            elif c == '[' and re.compile(r'\[=*\[').match(s, i):
                m = re.compile(r'\[(=*)\[').match(s, i)
                close = ']' + m.group(1) + ']'
                j = s.find(close, m.end())
                if j < 0:
                    raise LuauSyntaxError('unfinished long string')
                out.append(Token('string', s[i:j + len(close)], i, j + len(close)))
                i = j + len(close)
            elif c == '`':
                tok = self.interp(i)
                out.append(tok)
                i = tok.end
            else:
                for op in OPS:
                    if s.startswith(op, i):
                        break
                else:
                    raise LuauSyntaxError(f'unexpected character {c!r}')
                if op in '([{':
                    depth += 1
                elif op in ')]}':
                    depth -= 1
                out.append(Token('op', op, i, i + len(op)))
                i += len(op)

    def interp(self, i: int) -> Token:
        s = self.src
        parts: list = []
        j = i + 1
        lit_start = j
        while True:
            if j >= len(s) or s[j] == '\n':
                raise LuauSyntaxError('unfinished interpolated string')
            c = s[j]
            if c == '\\':
                j += 2
            elif c == '`':
                parts.append(('lit', lit_start, j))
                return Token('interp', s[i:j + 1], i, j + 1, parts)
            elif c == '{':
                parts.append(('lit', lit_start, j))
                toks, close = self.tokens(j + 1, stop_at_brace=True)
                parts.append(('expr', toks, j + 1, close))
                j = close + 1
                lit_start = j
            else:
                j += 1


@dataclasses.dataclass
class Node:
    kind: str
    start: int
    end: int
    children: list[Node] = dataclasses.field(default_factory=list)
    data: dict = dataclasses.field(default_factory=dict)


class Parser:
    def __init__(self, src: str, tokens: list[Token]) -> None:
        self.src = src
        self.toks = tokens
        self.i = 0

    # -- token helpers --
    @property
    def tok(self) -> Token:
        return self.toks[self.i]

    def peek(self, n: int = 1) -> Token:
        return self.toks[min(self.i + n, len(self.toks) - 1)]

    def is_op(self, *ops: str) -> bool:
        return self.tok.kind == 'op' and self.tok.value in ops

    def is_kw(self, *kws: str) -> bool:
        return self.tok.kind == 'keyword' and self.tok.value in kws

    def advance(self) -> Token:
        t = self.tok
        if t.kind != 'eof':
            self.i += 1
        return t

    def expect_op(self, op: str) -> Token:
        if not self.is_op(op):
            raise LuauSyntaxError(f'expected {op!r} near {self.tok.value!r} at {self.tok.start}')
        return self.advance()

    def expect_kw(self, kw: str) -> Token:
        if not self.is_kw(kw):
            raise LuauSyntaxError(f'expected {kw!r} near {self.tok.value!r} at {self.tok.start}')
        return self.advance()

    def expect_name(self) -> Token:
        if self.tok.kind != 'name':
            raise LuauSyntaxError(f'expected a name near {self.tok.value!r} at {self.tok.start}')
        return self.advance()

    @property
    def prev_end(self) -> int:
        return self.toks[self.i - 1].end

    # -- types: parsed only to be stripped --
    def skip_balanced(self, open_: str, close: str) -> None:
        self.expect_op(open_)
        depth = 1
        while depth:
            t = self.advance()
            if t.kind == 'eof':
                raise LuauSyntaxError('unbalanced brackets in type')
            if t.kind == 'op' and t.value == open_:
                depth += 1
            elif t.kind == 'op' and t.value == close:
                depth -= 1

    def skip_type(self) -> None:
        if self.is_op('|', '&'):
            self.advance()
        self.skip_type_unit()
        while True:
            if self.is_op('?'):
                self.advance()
            elif self.is_op('|', '&'):
                self.advance()
                self.skip_type_unit()
            else:
                return

    def skip_type_unit(self) -> None:
        t = self.tok
        if self.is_op('('):
            self.skip_balanced('(', ')')
            if self.is_op('->'):
                self.advance()
                self.skip_type()
        elif self.is_op('{'):
            self.skip_balanced('{', '}')
        elif self.is_op('<'):  # generic function type
            self.skip_balanced('<', '>')
            self.skip_balanced('(', ')')
            self.expect_op('->')
            self.skip_type()
        elif self.is_op('...'):
            self.advance()
            self.skip_type_unit()
        elif t.kind == 'name' and t.value == 'typeof' and self.peek().kind == 'op' and self.peek().value == '(':
            self.advance()
            self.skip_balanced('(', ')')
        elif t.kind == 'name':
            self.advance()
            if self.is_op('...'):  # generic pack `T...`
                self.advance()
                return
            while self.is_op('.'):
                self.advance()
                self.expect_name()
            if self.is_op('<'):
                self.skip_balanced('<', '>')
        elif t.kind in ('string', 'number') or self.is_kw('nil', 'true', 'false'):
            self.advance()
        else:
            raise LuauSyntaxError(f'bad type near {t.value!r} at {t.start}')

    def type_annotation(self) -> Node | None:
        '''`: Type` after a name/param list, returned as a span to strip.'''
        if not self.is_op(':'):
            return None
        start = self.advance().start
        self.skip_type()
        return Node('strip', start, self.prev_end)

    def generics(self) -> Node | None:
        if not self.is_op('<'):
            return None
        start = self.tok.start
        self.skip_balanced('<', '>')
        return Node('strip', start, self.prev_end)

    # -- blocks and statements --
    def chunk(self) -> Node:
        block = self.block()
        if self.tok.kind != 'eof':
            raise LuauSyntaxError(f'unexpected {self.tok.value!r} at {self.tok.start}')
        return block

    def block(self) -> Node:
        start = self.tok.start
        stmts: list[Node] = []
        while not (self.tok.kind == 'eof' or self.is_kw('end', 'else', 'elseif', 'until')):
            if self.is_kw('return'):
                stmts.append(self.return_stat())
                break
            st = self.statement()
            if st is not None:
                stmts.append(st)
        return Node('block', start, self.prev_end if stmts else start, stmts)

    def return_stat(self) -> Node:
        start = self.advance().start
        kids: list[Node] = []
        if not (self.tok.kind == 'eof' or self.is_kw('end', 'else', 'elseif', 'until') or self.is_op(';')):
            kids = self.expr_list()
        if self.is_op(';'):
            self.advance()
        return Node('return', start, self.prev_end, kids)

    def statement(self) -> Node | None:
        t = self.tok
        if self.is_op(';'):
            self.advance()
            return None
        if self.is_op('@'):
            return self.attributed()
        if self.is_kw('if'):
            return self.if_stat()
        if self.is_kw('while'):
            start = self.advance().start
            cond = self.expr()
            self.expect_kw('do')
            body = self.block()
            self.expect_kw('end')
            return Node('stat', start, self.prev_end, [cond, body])
        if self.is_kw('do'):
            start = self.advance().start
            body = self.block()
            self.expect_kw('end')
            return Node('stat', start, self.prev_end, [body])
        if self.is_kw('repeat'):
            start = self.advance().start
            body = self.block()
            self.expect_kw('until')
            cond = self.expr()
            return Node('stat', start, self.prev_end, [body, cond])
        if self.is_kw('for'):
            return self.for_stat()
        if self.is_kw('function'):
            start = self.advance().start
            kids = [self.func_name()]
            kids += self.func_body()
            return Node('stat', start, self.prev_end, kids)
        if self.is_kw('local'):
            return self.local_stat()
        if self.is_kw('break'):
            self.advance()
            return Node('stat', t.start, t.end)
        if t.kind == 'name' and t.value == 'continue' and not self.starts_suffix(self.peek()):
            self.advance()
            return Node('stat', t.start, t.end)
        if t.kind == 'name' and t.value == 'export' and self.peek().kind == 'name' and self.peek().value == 'type':
            return self.type_alias(t.start, skip=1)
        if t.kind == 'name' and t.value == 'type' and self.peek().kind == 'name' and self.peek(2).kind == 'op' and self.peek(2).value in ('=', '<'):
            return self.type_alias(t.start, skip=0)
        return self.expr_stat()

    @staticmethod
    def starts_suffix(t: Token) -> bool:
        return (t.kind == 'op' and t.value in ('(', '.', '[', ':', '=', ',', '{', '+=', '-=', '*=', '/=', '%=', '^=', '..=', '//=')) or t.kind == 'string'

    def type_alias(self, start: int, skip: int) -> Node:
        for _ in range(skip + 1):
            self.advance()  # `export`? `type`
        self.expect_name()
        self.generics()
        self.expect_op('=')
        self.skip_type()
        if self.is_op(';'):  # a `;` left behind on its own is a syntax error in 2021 Luau
            self.advance()
        return Node('strip', start, self.prev_end)

    def attributed(self) -> Node:
        kids: list[Node] = []
        while self.is_op('@'):
            start = self.advance().start
            if self.is_op('['):
                self.skip_balanced('[', ']')
            else:
                self.expect_name()
            kids.append(Node('strip', start, self.prev_end))
        st = self.statement()
        if st is None or not (st.kind == 'stat'):
            raise LuauSyntaxError('attribute must come before a function')
        return Node('stat', kids[0].start, st.end, kids + [st])

    def if_stat(self) -> Node:
        start = self.advance().start
        kids = [self.expr()]
        self.expect_kw('then')
        kids.append(self.block())
        while self.is_kw('elseif'):
            self.advance()
            kids.append(self.expr())
            self.expect_kw('then')
            kids.append(self.block())
        if self.is_kw('else'):
            self.advance()
            kids.append(self.block())
        self.expect_kw('end')
        return Node('stat', start, self.prev_end, kids)

    def binding(self) -> list[Node]:
        '''`name[: Type]` — returns the strip node, if any.'''
        self.expect_name()
        ann = self.type_annotation()
        return [ann] if ann else []

    def for_stat(self) -> Node:
        start = self.advance().start
        kids = self.binding()
        if self.is_op('='):
            self.advance()
            kids += self.expr_list()
            self.expect_kw('do')
            kids.append(self.block())
            self.expect_kw('end')
            return Node('stat', start, self.prev_end, kids)
        while self.is_op(','):
            self.advance()
            kids += self.binding()
        self.expect_kw('in')
        exprs = self.expr_list()
        if len(exprs) == 1 and not self.is_classic_iterator(exprs[0]):
            # generalised iteration: `for k, v in t` needs the table turned into an iterator
            e = exprs[0]
            exprs = [Node('iterwrap', e.start, e.end, [e])]
        kids += exprs
        self.expect_kw('do')
        kids.append(self.block())
        self.expect_kw('end')
        return Node('stat', start, self.prev_end, kids)

    def is_classic_iterator(self, e: Node) -> bool:
        '''`pairs(t)` / `ipairs(t)` already work on 2021, so they're left as they are.'''
        if e.kind != 'call' or e.data.get('method'):
            return False
        callee = e.children[0]
        return callee.kind == 'name' and self.src[callee.start:callee.end] in ('pairs', 'ipairs')

    def local_stat(self) -> Node:
        start = self.advance().start
        if self.is_kw('function'):
            self.advance()
            self.expect_name()
            kids = self.func_body()
            return Node('stat', start, self.prev_end, kids)
        kids = self.binding()
        while self.is_op(','):
            self.advance()
            kids += self.binding()
        if self.is_op('='):
            self.advance()
            kids += self.expr_list()
        return Node('stat', start, self.prev_end, kids)

    def func_name(self) -> Node:
        start = self.expect_name().start
        while self.is_op('.', ':'):
            self.advance()
            self.expect_name()
        return Node('raw', start, self.prev_end)

    def func_body(self) -> list[Node]:
        kids: list[Node] = []
        g = self.generics()
        if g:
            kids.append(g)
        self.expect_op('(')
        while not self.is_op(')'):
            if self.is_op('...'):
                self.advance()
            else:
                self.expect_name()
            ann = self.type_annotation()
            if ann:
                kids.append(ann)
            if not self.is_op(','):
                break
            self.advance()
        self.expect_op(')')
        ann = self.type_annotation()
        if ann:
            kids.append(ann)
        kids.append(self.block())
        self.expect_kw('end')
        return kids

    def expr_stat(self) -> Node:
        start = self.tok.start
        target = self.suffixed_expr()
        if self.tok.kind == 'op' and self.tok.value in COMPOUND:
            op = self.advance()
            rhs = self.expr()
            return Node('compound', start, self.prev_end, [target, rhs], {'op': COMPOUND[op.value], 'op_tok': op})
        if self.is_op('=', ','):
            kids = [target]
            while self.is_op(','):
                self.advance()
                kids.append(self.suffixed_expr())
            self.expect_op('=')
            kids += self.expr_list()
            return Node('stat', start, self.prev_end, kids)
        if target.kind != 'call':
            raise LuauSyntaxError(f'syntax error near {self.tok.value!r} at {self.tok.start}')
        return Node('stat', start, self.prev_end, [target])

    # -- expressions --
    def expr_list(self) -> list[Node]:
        out = [self.expr()]
        while self.is_op(','):
            self.advance()
            out.append(self.expr())
        return out

    def expr(self, limit: int = 0) -> Node:
        start = self.tok.start
        t = self.tok
        if (t.kind == 'keyword' and t.value == 'not') or (t.kind == 'op' and t.value in ('-', '#')):
            self.advance()
            operand = self.expr(UNARY_PRIORITY)
            left = Node('expr', start, self.prev_end, [operand])
        else:
            left = self.simple_expr()
        while True:
            t = self.tok
            op = t.value if t.kind in ('op', 'keyword') else None
            if op not in BINARY_PRIORITY or BINARY_PRIORITY[op][0] <= limit:
                return left
            self.advance()
            right = self.expr(BINARY_PRIORITY[op][1])
            kind = 'floordiv' if op == '//' else 'expr'
            left = Node(kind, left.start, self.prev_end, [left, right], {'op_tok': t})

    def simple_expr(self) -> Node:
        t = self.tok
        if t.kind == 'number':
            self.advance()
            node = Node('number', t.start, t.end, data={'text': t.value})
        elif t.kind == 'string' or self.is_kw('nil', 'true', 'false'):
            self.advance()
            node = Node('raw', t.start, t.end)
        elif t.kind == 'interp':
            self.advance()
            node = self.interp_node(t)
        elif self.is_op('...'):
            self.advance()
            node = Node('vararg', t.start, t.end)
        elif self.is_op('{'):
            node = self.table()
        elif self.is_kw('function'):
            start = self.advance().start
            node = Node('func', start, 0, self.func_body())
            node.end = self.prev_end
        elif self.is_kw('if'):
            node = self.if_expr()
        else:
            node = self.suffixed_expr()
        if self.is_op('::'):
            start = self.advance().start
            self.skip_type()
            node = Node('expr', node.start, self.prev_end, [node, Node('strip', start, self.prev_end)])
        return node

    def interp_node(self, t: Token) -> Node:
        kids: list[Node] = []
        for part in t.parts:
            if part[0] == 'expr':
                sub = Parser(self.src, part[1])
                e = sub.expr()
                if sub.tok.kind != 'eof':
                    raise LuauSyntaxError('bad expression in interpolated string')
                kids.append(e)
        return Node('interp', t.start, t.end, kids, {'parts': t.parts})

    def if_expr(self) -> Node:
        start = self.advance().start
        kids = [self.expr()]
        self.expect_kw('then')
        kids.append(self.expr())
        while self.is_kw('elseif'):
            self.advance()
            kids.append(self.expr())
            self.expect_kw('then')
            kids.append(self.expr())
        self.expect_kw('else')
        kids.append(self.expr())
        return Node('ifexpr', start, self.prev_end, kids)

    def table(self) -> Node:
        start = self.expect_op('{').start
        kids: list[Node] = []
        while not self.is_op('}'):
            if self.is_op('['):
                self.advance()
                kids.append(self.expr())
                self.expect_op(']')
                self.expect_op('=')
                kids.append(self.expr())
            elif self.tok.kind == 'name' and self.peek().kind == 'op' and self.peek().value == '=':
                self.advance()
                self.advance()
                kids.append(self.expr())
            else:
                kids.append(self.expr())
            if not self.is_op(',', ';'):
                break
            self.advance()
        self.expect_op('}')
        return Node('raw', start, self.prev_end, kids)

    def primary_expr(self) -> Node:
        t = self.tok
        if t.kind == 'name':
            self.advance()
            return Node('name', t.start, t.end)
        if self.is_op('('):
            start = self.advance().start
            e = self.expr()
            self.expect_op(')')
            return Node('paren', start, self.prev_end, [e])
        raise LuauSyntaxError(f'unexpected {t.value!r} at {t.start}')

    def suffixed_expr(self) -> Node:
        node = self.primary_expr()
        while True:
            if self.is_op('.'):
                self.advance()
                key = self.expect_name()
                node = Node('index', node.start, key.end, [node], {'field': key.value})
            elif self.is_op('['):
                self.advance()
                k = self.expr()
                self.expect_op(']')
                node = Node('index', node.start, self.prev_end, [node, k])
            elif self.is_op(':'):
                self.advance()
                self.expect_name()
                args = self.call_args()
                node = Node('call', node.start, self.prev_end, [node] + args, {'method': True})
            elif self.is_op('(', '{') or self.tok.kind == 'string':
                args = self.call_args()
                node = Node('call', node.start, self.prev_end, [node] + args)
            else:
                return node

    def call_args(self) -> list[Node]:
        if self.tok.kind == 'string':
            t = self.advance()
            return [Node('raw', t.start, t.end)]
        if self.is_op('{'):
            return [self.table()]
        self.expect_op('(')
        args: list[Node] = []
        if not self.is_op(')'):
            args = self.expr_list()
        self.expect_op(')')
        return args


# -- code generation --

PRELUDE_HELPERS = {
    'iter': (
        'local function __echo_iter(o, ...) if type(o) == "table" then local m = getmetatable(o) '
        'if type(m) == "table" and m.__iter then return m.__iter(o) end return next, o end return o, ... end'
    ),
    'floor': 'local __echo_floor = math.floor',
}

# Stand-ins for libraries newer than the 2021 engine; only added to scripts that use them.
RUNTIME_SHIMS = {
    'task': (
        # early-2021 builds have a partial `task` (no spawn/wait), so fill in each missing function
        'local task = (function(real) local t, gone = {}, setmetatable({}, {__mode = "k"}) '
        'local function run(co, ...) if gone[co] then return end local ok, e = coroutine.resume(co, ...) '
        'if not ok then spawn(function() error(e, 0) end) end end '
        'local function thread(f) return type(f) == "thread" and f or coroutine.create(f) end '
        'function t.wait(n) return (wait(n)) end '
        'function t.spawn(f, ...) local co = thread(f) run(co, ...) return co end '
        'function t.defer(f, ...) local co, a, n = thread(f), {...}, select("#", ...) spawn(function() run(co, unpack(a, 1, n)) end) return co end '
        'function t.delay(d, f, ...) local co, a, n = thread(f), {...}, select("#", ...) delay(d or 0, function() run(co, unpack(a, 1, n)) end) return co end '
        'function t.cancel(co) gone[co] = true end '
        'function t.synchronize() end function t.desynchronize() end '
        'if type(real) == "table" then for k, v in pairs(real) do t[k] = v end end return t end)(task)'
    ),
    'table': (
        'local table = (table.clone and table.freeze and table.isfrozen) and table or setmetatable({'
        'clone = table.clone or function(o) local c = {} for k, v in next, o do c[k] = v end local m = getmetatable(o) '
        'if type(m) == "table" then setmetatable(c, m) end return c end, '
        'freeze = table.freeze or function(o) return o end, '
        'isfrozen = table.isfrozen or function() return false end}, {__index = table})'
    ),
    'coroutine': (
        'local coroutine = coroutine.close and coroutine or setmetatable({'
        'close = function() return true end}, {__index = coroutine})'
    ),
}
SHIM_TRIGGERS = {
    'task': re.compile(r'(?<![\w.:])task\s*\.'),
    'table': re.compile(r'(?<![\w.:])table\s*\.\s*(?:clone|freeze|isfrozen)\b'),
    'coroutine': re.compile(r'(?<![\w.:])coroutine\s*\.\s*close\b'),
}


def _keep_lines(text: str) -> str:
    '''What a removed span turns into: its newlines, or a space.'''
    n = text.count('\n')
    return '\n' * n if n else ' '


def _lua_string_from_interp(raw: str) -> str:
    out = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == '\\' and i + 1 < len(raw):
            nxt = raw[i + 1]
            out.append(nxt if nxt in '`{}' else c + nxt)
            i += 2
            continue
        out.append('\\"' if c == '"' else c)
        i += 1
    return '"' + ''.join(out) + '"'


def _normalise_number(text: str) -> str:
    clean = text.replace('_', '')
    if clean[:2].lower() == '0b':
        return str(int(clean[2:], 2))
    return clean


class Generator:
    def __init__(self, src: str) -> None:
        self.src = src
        self.helpers: set[str] = set()
        self.temp = 0

    def gen(self, n: Node) -> str:
        custom = getattr(self, 'gen_' + n.kind, None)
        if custom is None:
            return self.splice(n)
        out = custom(n)
        # rewrites may drop line breaks from the original span; put them back at the end
        missing = self.src[n.start:n.end].count('\n') - out.count('\n')
        return out + '\n' * missing if missing > 0 else out

    def splice(self, n: Node) -> str:
        out = []
        pos = n.start
        for c in n.children:
            out.append(self.src[pos:c.start])
            out.append(self.gen(c))
            pos = c.end
        out.append(self.src[pos:n.end])
        return ''.join(out)

    def gen_strip(self, n: Node) -> str:
        return _keep_lines(self.src[n.start:n.end])

    def gen_number(self, n: Node) -> str:
        return _normalise_number(n.data['text'])

    def gen_interp(self, n: Node) -> str:
        pieces = []
        kids = iter(n.children)
        for part in n.data['parts']:
            if part[0] == 'lit':
                if part[2] > part[1]:
                    pieces.append(_lua_string_from_interp(self.src[part[1]:part[2]]))
            else:
                pieces.append('tostring(' + self.gen(next(kids)) + ')')
        if not pieces:
            return '""'
        return '(' + ' .. '.join(pieces) + ')'

    def gen_floordiv(self, n: Node) -> str:
        self.helpers.add('floor')
        left, right = n.children
        op = n.data['op_tok']
        return (
            '__echo_floor((' + self.gen(left) + ')' + self.src[left.end:op.start] + '/'
            + self.src[op.end:right.start] + '(' + self.gen(right) + '))'
        )

    def gen_iterwrap(self, n: Node) -> str:
        self.helpers.add('iter')
        return '__echo_iter(' + self.gen(n.children[0]) + ')'

    def gen_ifexpr(self, n: Node) -> str:
        kids = n.children
        va = '...' if _uses_vararg(n) else ''
        out = [f'(function({va}) if ', self.gen(kids[0]), ' then return (', self.gen(kids[1]), ')']
        for j in range(2, len(kids) - 1, 2):
            out += [' elseif ', self.gen(kids[j]), ' then return (', self.gen(kids[j + 1]), ')']
        out += [' else return (', self.gen(kids[-1]), f') end end)({va})']
        return ''.join(out)

    def gen_compound(self, n: Node) -> str:
        target, rhs = n.children
        op = n.data['op']
        value = self.gen(rhs)
        combine = (lambda cur: f'__echo_floor(({cur}) / ({value}))') if op == '//' else (lambda cur: f'{cur} {op} ({value})')
        if op == '//':
            self.helpers.add('floor')
        if target.kind == 'name':
            name = self.src[target.start:target.end]
            return f'{name} = {combine(name)}'
        if target.kind != 'index':
            raise LuauSyntaxError('cannot assign to this expression')
        self.temp += 1
        obj = f'__echo_o{self.temp}'
        if 'field' in target.data:
            slot = f'{obj}.{target.data["field"]}'
            return f'do local {obj} = {self.gen(target.children[0])}; {slot} = {combine(slot)} end'
        key = f'__echo_k{self.temp}'
        slot = f'{obj}[{key}]'
        return (
            f'do local {obj}, {key} = {self.gen(target.children[0])}, {self.gen(target.children[1])}; '
            f'{slot} = {combine(slot)} end'
        )


def _uses_vararg(n: Node) -> bool:
    for c in n.children:
        if c.kind == 'vararg':
            return True
        if c.kind != 'func' and _uses_vararg(c):
            return True
    return False


def downgrade(source: str) -> str:
    '''Returns `source` rewritten for the 2021 engine, or unchanged if it can't be parsed.'''
    try:
        tokens, _ = Lexer(source).tokens()
        # the root spans the whole file so leading/trailing comments and blank lines survive
        tree = Node('root', 0, len(source), [Parser(source, tokens).chunk()])
        g = Generator(source)
        body = g.gen(tree)
    except (LuauSyntaxError, RecursionError, IndexError, StopIteration, ValueError):
        return source
    prelude = [PRELUDE_HELPERS[h] for h in sorted(g.helpers)]
    prelude += [code for name, code in RUNTIME_SHIMS.items() if SHIM_TRIGGERS[name].search(source)]
    if not prelude and body == source:
        return source
    # everything goes on line 1 so the rest of the script keeps its line numbers
    return '; '.join(prelude) + ('; ' if prelude else '') + body
