import unittest

from assets.serialisers.luau import downgrade


class TestLuauDowngrade(unittest.TestCase):
    def check(self, src: str, expected: str) -> None:
        out = downgrade(src)
        self.assertEqual(out, expected)
        self.assertEqual(out.count('\n'), src.count('\n'), 'line count must not change')

    def test_unchanged_code_is_identical(self) -> None:
        src = 'local x = 1 -- hi\nprint(x .. "a", #t, not y)\nfor i = 1, 10 do end\n'
        self.assertIs(downgrade(src), src)

    def test_interpolation(self) -> None:
        self.check('print(`hi {name}!`)', 'print(("hi " .. tostring(name) .. "!"))')
        self.check('x = `{a}{b}`', 'x = (tostring(a) .. tostring(b))')
        self.check('x = `plain`', 'x = ("plain")')
        self.check('x = ``', 'x = ""')
        self.check('x = `a \\{b\\} \\` "q" {1 + 2}`', 'x = ("a {b} ` \\"q\\" " .. tostring(1 + 2))')
        self.check('x = `{ ({1})[1] }`', 'x = (tostring(({1})[1]))')
        self.check('x = `{`in {y}`}`', 'x = (tostring(("in " .. tostring(y))))')

    def test_floor_division(self) -> None:
        self.check('x = a // b', 'local __echo_floor = math.floor; x = __echo_floor((a) / (b))')
        self.check('x = 1 + a // 2 * 3', 'local __echo_floor = math.floor; x = 1 + __echo_floor((a) / (2)) * 3')
        self.check('x //= 2', 'local __echo_floor = math.floor; x = __echo_floor((x) / (2))')

    def test_compound(self) -> None:
        self.check('x += 1', 'x = x + (1)')
        self.check('s ..= "a" .. b', 's = s .. ("a" .. b)')
        self.check('a.b.c -= f()', 'do local __echo_o1 = a.b; __echo_o1.c = __echo_o1.c - (f()) end')
        self.check('t[k()] *= 2', 'do local __echo_o1, __echo_k1 = t, k(); __echo_o1[__echo_k1] = __echo_o1[__echo_k1] * (2) end')
        self.check('x += `{y}`', 'x = x + ((tostring(y)))')

    def test_if_expression(self) -> None:
        self.check('x = if a then 1 else 2', 'x = (function() if a then return (1) else return (2) end end)()')
        self.check(
            'x = if a then 1 elseif b then 2 else 3',
            'x = (function() if a then return (1) elseif b then return (2) else return (3) end end)()',
        )
        self.check(
            'local function f(...) return if ... then ... else 0 end',
            'local function f(...) return (function(...) if ... then return (...) else return (0) end end)(...) end',
        )
        # `...` inside a nested function doesn't make the outer one vararg
        self.check(
            'x = if a then function(...) return ... end else nil',
            'x = (function() if a then return (function(...) return ... end) else return (nil) end end)()',
        )
        self.check('x = if a\nthen 1\nelse 2', 'x = (function() if a then return (1) else return (2) end end)()\n\n')

    def test_generalised_iteration(self) -> None:
        out = downgrade('for k, v in t do end')
        self.assertTrue(out.endswith('for k, v in __echo_iter(t) do end'))
        self.assertTrue(out.startswith('local function __echo_iter('))
        # pairs/ipairs already work on 2021 and stay untouched
        self.assertIs(downgrade('for k, v in pairs(t) do end'), 'for k, v in pairs(t) do end')
        self.assertIs(downgrade('for i, v in ipairs(t) do end'), 'for i, v in ipairs(t) do end')
        self.assertIn('__echo_iter(x:pairs())', downgrade('for k, v in x:pairs() do end'))
        self.assertIn('__echo_iter(p:GetPlayers())', downgrade('for _, p in p:GetPlayers() do end'))
        # several expressions: already an iterator triple
        self.assertIs(downgrade('for k, v in next, t do end'), 'for k, v in next, t do end')

    def test_types_are_stripped(self) -> None:
        self.check('local x: number = 1', 'local x  = 1')
        self.check(
            'local function f<T>(a: T, b: {[string]: number}?, ...: any): (T, string) return a end',
            'local function f (a , b , ... )  return a end',
        )
        self.check('type Map<K, V> = {[K]: V}\nprint(1)', ' \nprint(1)')
        self.check('export type Fn = (number, string) -> ...boolean\nprint(1)', ' \nprint(1)')
        self.check('local y = (x :: any).z', 'local y = (x  ).z')
        self.check('local a: typeof(b) | nil, c: "x" | "y" = 1, 2', 'local a , c  = 1, 2')
        self.check('for i: number = 1, 2 do end', 'for i  = 1, 2 do end')
        self.check('type T = {\n  a: number,\n}\nprint(1)', '\n\n\nprint(1)')
        # the trailing `;` goes too: a lone `;` doesn't parse on 2021
        self.check('type A = number;\nexport type B = string;\nprint(1)', ' \n \nprint(1)')
        # `type` used as a normal name still works
        src = 'print(type(x))\nlocal type = 1'
        self.assertIs(downgrade(src), src)

    def test_numbers_and_attributes(self) -> None:
        self.check('x = 0b1010 + 1_000 + 0xFF_FF', 'x = 10 + 1000 + 0xFFFF')
        self.check('@native\nlocal function f() end', ' \nlocal function f() end')

    def test_continue_is_kept(self) -> None:
        src = 'for i = 1, 3 do if i == 2 then continue end end\ncontinue = 1'
        self.assertIs(downgrade(src), src)

    def test_shims_only_when_used(self) -> None:
        self.assertTrue(downgrade("task.wait(1)").startswith("local task = (function(real)"))
        self.assertTrue(downgrade("local c = table.clone(t)").startswith("local table = (table.clone"))
        self.assertIs(downgrade('table.insert(t, 1)'), 'table.insert(t, 1)')
        self.assertIs(downgrade('mytask.wait(1)'), 'mytask.wait(1)')

    def test_lines_stay_put(self) -> None:
        src = 'local x: {\n a: number\n} = {a = 1}\nlocal s = `{x.a}`\nx.a += 1\nerror("line 6")\n'
        out = downgrade(src)
        self.assertEqual(out.split('\n')[5], 'error("line 6")')

    def test_unparseable_is_left_alone(self) -> None:
        src = 'local = = ='
        self.assertIs(downgrade(src), src)


if __name__ == '__main__':
    unittest.main()
