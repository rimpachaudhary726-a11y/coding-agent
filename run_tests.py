import sys
from calculator import add, subtract, multiply, divide

def assert_equal(a, b, msg=''):
    if a != b:
        raise AssertionError(f'{msg}: {a} != {b}')

# tests
assert_equal(add(2,3),5,'add')
assert_equal(add(-1,1),0,'add')
assert_equal(subtract(5,3),2,'sub')
assert_equal(subtract(0,5),-5,'sub')
assert_equal(multiply(4,5),20,'mul')
assert_equal(multiply(-2,3),-6,'mul')
assert_equal(divide(10,2),5,'div')
assert_equal(divide(-9,3),-3,'div')
assert_equal(divide(5,2),2.5,'div')
# divide by zero
try:
    divide(1,0)
    raise AssertionError('divide by zero did not raise')
except ValueError:
    pass
print('All tests passed')
