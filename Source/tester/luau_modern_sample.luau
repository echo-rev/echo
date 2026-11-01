--!strict
-- Behaviour check for assets/serialisers/luau: run the *converted* version of this on a
-- 2021 engine (Hexium Studio / RFD 2021). It returns "ALL PASSED" or the failing checks.
local results = {}
local function check(name: string, got: any, want: any)
	if got ~= want then
		table.insert(results, name .. ": got " .. tostring(got) .. ", want " .. tostring(want))
	end
end

-- types
type Point = { x: number, y: number }
export type Fn<T> = (T) -> T
local p: Point = { x = 3, y = 4 }
local function add<T>(a: number, b: number): number
	return a + b
end
check("types", add(p.x, p.y), 7)
check("cast", (p :: any).x, 3)

-- string interpolation
local name = "Echo"
check("interp", `hi {name} {1 + 2} \{x}`, "hi Echo 3 {x}")
check("interp nested", `a{`b{name}`}`, "abEcho")
check("interp tostring", `{nil}-{true}`, "nil-true")

-- if-expressions
local n = 5
check("ifexpr", if n > 3 then "big" elseif n > 1 then "mid" else "small", "big")
local function va(...): any
	return if select("#", ...) > 0 then ... else "none"
end
check("ifexpr vararg", va("x"), "x")
check("ifexpr empty vararg", va(), "none")
check("ifexpr short-circuit", if true then 1 else error("boom"), 1)

-- compound assignment
local c = 10
c += 5
c -= 1
c *= 2
c /= 4
c ^= 2
check("compound", c, 49)
local s = "a"
s ..= "b"
check("concat assign", s, "ab")
local calls = 0
local t = { arr = { 1, 2, 3 } }
local function key()
	calls += 1
	return 2
end
t.arr[key()] += 10
check("index compound evaluates key once", calls, 1)
check("index compound value", t.arr[2], 12)

-- floor division
check("floordiv", 7 // 2, 3)
check("floordiv negative", -7 // 2, -4)
local f = 17
f //= 5
check("floordiv assign", f, 3)

-- generalised iteration
local sum = 0
for _, v in { 1, 2, 3 } do
	sum += v
end
check("generalised iter", sum, 6)
local keys = 0
for k, v in { a = 1, b = 2 } do
	keys += 1
end
check("generalised iter dict", keys, 2)
local count = 0
for _, child in workspace:GetChildren() do
	count += 1
end
check("iter over method call", count, #workspace:GetChildren())
local custom = setmetatable({}, {
	__iter = function()
		local i = 0
		return function()
			i += 1
			if i <= 3 then
				return i
			end
			return nil
		end
	end,
})
local itsum = 0
for i in custom do
	itsum += i
end
check("__iter metamethod", itsum, 6)
local psum = 0
for _, v in pairs({ 4, 5 }) do
	psum += v
end
check("pairs untouched", psum, 9)

-- number literals
check("binary literal", 0b1010, 10)
check("underscores", 1_000_000, 1000000)
check("hex underscores", 0xFF_FF, 65535)

-- continue
local odd = 0
for i = 1, 10 do
	if i % 2 == 0 then
		continue
	end
	odd += 1
end
check("continue", odd, 5)

-- table library stand-ins
local orig = setmetatable({ 1, 2, { 3 } }, { __index = function() return "mt" end })
local copy = table.clone(orig)
check("clone copies", copy[2], 2)
check("clone is shallow", copy[3], orig[3])
check("clone keeps metatable", copy.missing, "mt")
check("freeze returns table", table.freeze({}) ~= nil, true)

-- task library stand-ins
local order = {}
task.spawn(function(x)
	table.insert(order, "spawn" .. x)
end, 1)
check("task.spawn runs now", order[1], "spawn1")
local deferred = false
task.defer(function()
	deferred = true
end)
check("task.defer waits", deferred, false)
local delayed = false
task.delay(0.05, function()
	delayed = true
end)
local cancelled = false
local victim = task.delay(0.05, function()
	cancelled = true
end)
task.cancel(victim)
local waited = task.wait(0.3)
check("task.wait returns time", type(waited), "number")
check("task.defer ran", deferred, true)
check("task.delay ran", delayed, true)
check("task.cancel", cancelled, false)
local resumed = false
task.spawn(function()
	task.wait(0.05)
	resumed = true
end)
task.wait(0.3)
check("task.wait inside task.spawn", resumed, true)

-- attributes
@native
local function nat()
	return 1
end
check("attribute", nat(), 1)

return #results == 0 and "ALL PASSED" or table.concat(results, "\n")
