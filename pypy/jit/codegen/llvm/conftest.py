import py
from pypy.jit.codegen import detect_cpu

#XXX Should check here if llvm supports a JIT for this platform (perhaps using lli?)

class Directory(py.test.collect.Directory):

    def run(self):
        py.test.skip("in-progress")
#        try:
#            processor = detect_cpu.autodetect()
#        except detect_cpu.ProcessorAutodetectError, e:
#            py.test.skip(str(e))
#        else:
#            if processor != 'i386':
#                py.test.skip('detected a %r CPU' % (processor,))
#
#        return super(Directory, self).run()

Option = py.test.Config.Option

option = py.test.Config.addoptions("llvm options",
        Option('--lineno', action="store_true", default=False,
               dest="lineno",
               help="add linenumbers to the generated code"),

        Option('--print-source', action="store_true", default=False,
               dest="print_source",
               help="print generated sources"),

        Option('--print-debug', action="store_true", default=False,
               dest="print_debug",
               help="print debug information"))
