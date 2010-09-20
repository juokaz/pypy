from pypy.interpreter.gateway import ObjSpace, W_Root, unwrap_spec, interp2app
from pypy.interpreter.function import StaticMethod
from pypy.interpreter.error import wrap_windowserror
from pypy.rlib import rwin32
from pypy.rlib.rarithmetic import r_uint
from pypy.rpython.lltypesystem import rffi, lltype
from pypy.translator.tool.cbuild import ExternalCompilationInfo
from pypy.rpython.tool import rffi_platform

CONSTANTS = """
    PIPE_ACCESS_INBOUND PIPE_ACCESS_DUPLEX
    GENERIC_READ GENERIC_WRITE OPEN_EXISTING
    PIPE_TYPE_MESSAGE PIPE_READMODE_MESSAGE PIPE_WAIT
    PIPE_UNLIMITED_INSTANCES
    NMPWAIT_WAIT_FOREVER
    ERROR_PIPE_CONNECTED ERROR_SEM_TIMEOUT ERROR_PIPE_BUSY
""".split()

class CConfig:
    _compilation_info_ = ExternalCompilationInfo(
        includes = ['windows.h'],
        libraries = ['kernel32'],
        )

    for name in CONSTANTS:
        locals()[name] = rffi_platform.ConstantInteger(name)

class cConfig:
    pass
cConfig.__dict__.update(rffi_platform.configure(CConfig))

def handle_w(space, w_handle):
    return rffi.cast(rwin32.HANDLE, space.int_w(w_handle))
def w_handle(space, handle):
    return space.wrap(rffi.cast(rffi.INTPTR_T, handle))

_CreateNamedPipe = rwin32.winexternal(
    'CreateNamedPipeA', [
        rwin32.LPCSTR,
        rwin32.DWORD, rwin32.DWORD, rwin32.DWORD,
        rwin32.DWORD, rwin32.DWORD, rwin32.DWORD,
        rffi.VOIDP],
    rwin32.HANDLE)

_ConnectNamedPipe = rwin32.winexternal(
    'ConnectNamedPipe', [rwin32.HANDLE, rffi.VOIDP], rwin32.BOOL)

_SetNamedPipeHandleState = rwin32.winexternal(
    'SetNamedPipeHandleState', [
        rwin32.HANDLE,
        rwin32.LPDWORD, rwin32.LPDWORD, rwin32.LPDWORD],
    rwin32.BOOL)

_CreateFile = rwin32.winexternal(
    'CreateFileA', [
        rwin32.LPCSTR,
        rwin32.DWORD, rwin32.DWORD, rffi.VOIDP,
        rwin32.DWORD, rwin32.DWORD, rwin32.HANDLE],
    rwin32.HANDLE)

_ExitProcess = rwin32.winexternal(
    'ExitProcess', [rffi.UINT], lltype.Void)

def CloseHandle(space, w_handle):
    handle = handle_w(space, w_handle)
    if not rwin32.CloseHandle(handle):
        raise wrap_windowserror(space, rwin32.lastWindowsError())

def GetLastError(space):
    return space.wrap(rwin32.lastWindowsError())

@unwrap_spec(ObjSpace, str, r_uint, r_uint, r_uint, r_uint, r_uint, r_uint, W_Root)
def CreateNamedPipe(space, name, openmode, pipemode, maxinstances,
                    outputsize, inputsize, timeout, w_security):
    security = space.int_w(w_security)
    if security:
        raise OperationError(space.w_NotImplementedError,
                             space.wrap("expected a NULL pointer"))
    handle = _CreateNamedPipe(
        name, openmode, pipemode, maxinstances,
        outputsize, inputsize, timeout, rffi.NULL)

    if handle == rwin32.INVALID_HANDLE_VALUE:
        raise wrap_windowserror(space, rwin32.lastWindowsError())

    return w_handle(space, handle)

def ConnectNamedPipe(space, w_handle, w_overlapped):
    handle = handle_w(space, w_handle)
    overlapped = space.int_w(w_overlapped)
    if overlapped:
        raise OperationError(space.w_NotImplementedError,
                             space.wrap("expected a NULL pointer"))
    if not _ConnectNamedPipe(handle, rffi.NULL):
        raise wrap_windowserror(space, rwin32.lastWindowsError())

@unwrap_spec(ObjSpace, W_Root, W_Root, W_Root, W_Root)
def SetNamedPipeHandleState(space, w_handle, w_pipemode, w_maxinstances, w_timeout):
    handle = handle_w(space, w_handle)
    state = lltype.malloc(rffi.CArrayPtr(rffi.UINT).TO, 3, flavor='raw')
    statep = lltype.malloc(rffi.CArrayPtr(rffi.UINTP).TO, 3, flavor='raw', zero=True)
    try:
        if not space.is_w(w_pipemode, space.w_None):
            state[0] = space.uint_w(w_pipemode)
            statep[0] = rffi.ptradd(state, 0)
        if not space.is_w(w_maxinstances, space.w_None):
            state[1] = space.uint_w(w_maxinstances)
            statep[1] = rffi.ptradd(state, 1)
        if not space.is_w(w_timeout, space.w_None):
            state[2] = space.uint_w(w_timeout)
            statep[2] = rffi.ptradd(state, 2)
        if not _SetNamedPipeHandleState(handle, statep[0], statep[1], statep[2]):
            raise wrap_windowserror(space, rwin32.lastWindowsError())
    finally:
        lltype.free(state, flavor='raw')
        lltype.free(statep, flavor='raw')

@unwrap_spec(ObjSpace, str, r_uint, r_uint, W_Root, r_uint, r_uint, W_Root)
def CreateFile(space, filename, access, share, w_security,
               disposition, flags, w_templatefile):
    security = space.int_w(w_security)
    templatefile = space.int_w(w_templatefile)
    if security or templatefile:
        raise OperationError(space.w_NotImplementedError,
                             space.wrap("expected a NULL pointer"))

    handle = _CreateFile(filename, access, share, rffi.NULL,
                         disposition, flags, rwin32.NULL_HANDLE)

    if handle == rwin32.INVALID_HANDLE_VALUE:
        raise wrap_windowserror(space, rwin32.lastWindowsError())

    return w_handle(space, handle)

@unwrap_spec(ObjSpace, r_uint)
def ExitProcess(space, code):
    _ExitProcess(code)

def win32_namespace(space):
    "NOT_RPYTHON"
    w_win32 = space.call_function(space.w_type,
                                  space.wrap("win32"),
                                  space.newtuple([]),
                                  space.newdict())
    # constants
    for name in CONSTANTS:
        space.setattr(w_win32,
                      space.wrap(name),
                      space.wrap(getattr(cConfig, name)))
    space.setattr(w_win32,
                  space.wrap('NULL'),
                  space.newint(0))

    # functions
    for name in ['CloseHandle', 'GetLastError', 'CreateFile',
                 'CreateNamedPipe', 'ConnectNamedPipe',
                 'SetNamedPipeHandleState',
                 'ExitProcess',
                 ]:
        function = globals()[name]
        w_function = space.wrap(interp2app(function))
        w_method = space.wrap(StaticMethod(w_function))
        space.setattr(w_win32, space.wrap(name), w_method)

    return w_win32
