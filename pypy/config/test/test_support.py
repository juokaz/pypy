
from cStringIO import StringIO
from pypy.config.support import detect_number_of_processors
import os, sys, py

cpuinfo = """
processor\t: 0

processor\t: 1
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 37
model name\t: Intel(R) Core(TM) i7 CPU       L 620  @ 2.00GHz
stepping\t: 2

processor\t: 2
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 37
model name\t: Intel(R) Core(TM) i7 CPU       L 620  @ 2.00GHz
stepping\t: 2

processor\t: 3
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 37
model name\t: Intel(R) Core(TM) i7 CPU       L 620  @ 2.00GHz
stepping\t: 2
cpu MHz\t\t: 1199.000
cache size\t: 4096 KB
physical id\t: 0
siblings\t: 4
"""

class FakeEnviron:
    def __init__(self, value):
        self._value = value
    def get(self, varname):
        assert varname == 'MAKEFLAGS'
        return self._value

def test_cpuinfo():
    if sys.platform != 'linux2':
        py.test.skip("linux only")
    saved = os.environ
    try:
        os.environ = FakeEnviron(None)
        assert detect_number_of_processors(StringIO(cpuinfo)) == 4
        assert detect_number_of_processors('random crap that does not exist') == 1
        os.environ = FakeEnviron('-j2')
        assert detect_number_of_processors(StringIO(cpuinfo)) == 1
    finally:
        os.environ = saved
