"""
The Bookkeeper class.
"""

from __future__ import generators
from types import FunctionType, ClassType, MethodType
from types import BuiltinMethodType
from pypy.tool.ansi_print import ansi_print
from pypy.annotation.model import *
from pypy.annotation.classdef import ClassDef
from pypy.annotation.listdef import ListDef, MOST_GENERAL_LISTDEF
from pypy.annotation.dictdef import DictDef, MOST_GENERAL_DICTDEF
from pypy.tool.hack import func_with_new_name
from pypy.interpreter.pycode import CO_VARARGS
from pypy.interpreter.pycode import cpython_code_signature
from pypy.interpreter.argument import ArgErr
from pypy.tool.rarithmetic import r_uint
from pypy.tool.unionfind import UnionFind

import inspect, new

class PBCAccessSet:
    def __init__(self, obj):
        self.objects = { obj: True }
        self.read_locations = {}
        self.attrs = {}

    def update(self, other):
        self.objects.update(other.objects)
        self.read_locations.update(other.read_locations)        
        self.attrs.update(other.attrs)


class Bookkeeper:
    """The log of choices that have been made while analysing the operations.
    It ensures that the same 'choice objects' will be returned if we ask
    again during reflowing.  Like ExecutionContext, there is an implicit
    Bookkeeper that can be obtained from a thread-local variable.

    Currently used for factories and user-defined classes."""

    def __init__(self, annotator):
        self.annotator = annotator
        self.userclasses = {}    # map classes to ClassDefs
        self.userclasseslist = []# userclasses.keys() in creation order
        self.cachespecializations = {}
        self.pbccache = {}
        self.pbctypes = {}
        self.seen_mutable = {}
        self.listdefs = {}       # map position_keys to ListDefs
        self.dictdefs = {}       # map position_keys to DictDefs
        
        # mapping position -> most general result, for call sites calling
        # argtypes specialized functions
        self.argtypes_spec_callsite_results = {}

        self.pbc_maximal_access_sets = UnionFind(PBCAccessSet)
        
        # import ordering hack
        global BUILTIN_ANALYZERS
        from pypy.annotation.builtin import BUILTIN_ANALYZERS

    def enter(self, position_key):
        """Start of an operation.
        The operation is uniquely identified by the given key."""
        self.position_key = position_key
        TLS.bookkeeper = self

    def leave(self):
        """End of an operation."""
        del TLS.bookkeeper
        del self.position_key

    def getclassdef(self, cls):
        """Get the ClassDef associated with the given user cls."""
        if cls is object:
            return None
        try:
            return self.userclasses[cls]
        except KeyError:
            if cls in self.pbctypes:
                self.warning("%r gets a ClassDef, but is the type of some PBC"
                             % (cls,))
            cdef = ClassDef(cls, self)
            self.userclasses[cls] = cdef
            self.userclasseslist.append(cdef)
            return self.userclasses[cls]

    def getlistdef(self):
        """Get the ListDef associated with the current position."""
        try:
            listdef = self.listdefs[self.position_key]
        except KeyError:
            listdef = self.listdefs[self.position_key] = ListDef(self)
        return listdef

    def newlist(self, *s_values):
        """Make a SomeList associated with the current position, general
        enough to contain the s_values as items."""
        listdef = self.getlistdef()
        for s_value in s_values:
            listdef.generalize(s_value)
        return SomeList(listdef)

    def getdictdef(self):
        """Get the DictDef associated with the current position."""
        try:
            dictdef = self.dictdefs[self.position_key]
        except KeyError:
            dictdef = self.dictdefs[self.position_key] = DictDef(self)
        return dictdef

    def newdict(self, *items_s):
        """Make a SomeDict associated with the current position, general
        enough to contain the given (s_key, s_value) as items."""
        dictdef = self.getdictdef()
        for s_key, s_value in items_s:
            dictdef.generalize_key(s_key)
            dictdef.generalize_value(s_value)
        return SomeDict(dictdef)


    def immutablevalue(self, x):
        """The most precise SomeValue instance that contains the
        immutable value x."""
        tp = type(x)
        if tp is bool:
            result = SomeBool()
        elif tp is int:
            result = SomeInteger(nonneg = x>=0)
        elif tp is r_uint:
            result = SomeInteger(nonneg = True, unsigned = True)
        elif issubclass(tp, str): # py.lib uses annotated str subclasses
            result = SomeString()
        elif tp is tuple:
            result = SomeTuple(items = [self.immutablevalue(e) for e in x])
        elif tp is float:
            result = SomeFloat()
        elif tp is list:
            items_s = [self.immutablevalue(e) for e in x]
            result = SomeList(ListDef(self, unionof(*items_s)))
        elif tp is dict:   # exactly a dict
            keys_s   = [self.immutablevalue(e) for e in x.keys()]
            values_s = [self.immutablevalue(e) for e in x.values()]
            result = SomeDict(DictDef(self, unionof(*keys_s),
                                            unionof(*values_s)))
        elif ishashable(x) and x in BUILTIN_ANALYZERS:
            result = SomeBuiltin(BUILTIN_ANALYZERS[x])
        elif callable(x) or isinstance(x, staticmethod): # XXX
            # maybe 'x' is a method bound to a not-yet-frozen cache?
            # fun fun fun.
            if hasattr(x, 'im_self') and hasattr(x.im_self, '_freeze_'):
                x.im_self._freeze_()
            if hasattr(x, '__self__') and x.__self__ is not None:
                s_self = self.immutablevalue(x.__self__)
                try:
                    result = s_self.find_method(x.__name__)
                except AttributeError:
                    result = SomeObject()
            else:
                return self.getpbc(x)
        elif hasattr(x, '__class__') \
                 and x.__class__.__module__ != '__builtin__':
            # user-defined classes can define a method _freeze_(), which
            # is called when a prebuilt instance is found.  If the method
            # returns True, the instance is considered immutable and becomes
            # a SomePBC().  Otherwise it's just SomeInstance().
            frozen = hasattr(x, '_freeze_') and x._freeze_()
            if frozen:
                return self.getpbc(x)
            else:
                clsdef = self.getclassdef(x.__class__)
                
                if x not in self.seen_mutable: # avoid circular reflowing, 
                                               # see for example test_circular_mutable_getattr
                    for attr in x.__dict__:
                        clsdef.add_source_for_attribute(attr, x) # can trigger reflowing
                    self.seen_mutable[x] = True
                return SomeInstance(clsdef)
        elif x is None:
            return self.getpbc(None)
        else:
            result = SomeObject()
        result.const = x
        return result

    def getpbc(self, x):
        try:
            # this is not just an optimization, but needed to avoid
            # infinitely repeated calls to add_source_for_attribute()
            return self.pbccache[x]
        except KeyError:
            result = SomePBC({x: True}) # pre-built inst
            #clsdef = self.getclassdef(new_or_old_class(x))
            #for attr in getattr(x, '__dict__', {}):
            #    clsdef.add_source_for_attribute(attr, x)
            self.pbccache[x] = result
            cls = new_or_old_class(x)
            if cls not in self.pbctypes:
                self.pbctypes[cls] = True
                if cls in self.userclasses:
                    self.warning("making some PBC of type %r, which has "
                                 "already got a ClassDef" % (cls,))
            return result

    def valueoftype(self, t):
        """The most precise SomeValue instance that contains all
        objects of type t."""
        assert isinstance(t, (type, ClassType))
        if t is bool:
            return SomeBool()
        elif t is int:
            return SomeInteger()
        elif t is r_uint:
            return SomeInteger(nonneg = True, unsigned = True)
        elif issubclass(t, str): # py.lib uses annotated str subclasses
            return SomeString()
        elif t is float:
            return SomeFloat()
        elif t is list:
            return SomeList(MOST_GENERAL_LISTDEF)
        elif t is dict:
            return SomeDict(MOST_GENERAL_DICTDEF)
        # can't do dict, tuple
        elif t.__module__ != '__builtin__':
            classdef = self.getclassdef(t)
            return SomeInstance(classdef)
        else:
            o = SomeObject()
            o.knowntype = t
            return o

    def pbc_getattr(self, pbc, s_attr):
        assert s_attr.is_constant()
        attr = s_attr.const

        access_sets = self.pbc_maximal_access_sets
        objects = pbc.prebuiltinstances.keys()

        change, rep, access = access_sets.find(objects[0])
        for obj in objects:
            change1, rep, access = access_sets.union(rep, obj)
            change = change or change1

        access.attrs[attr] = True
        position = self.position_key
        access.read_locations[position] = True

        actuals = []

        for c in access.objects:
            if hasattr(c, attr):
                actuals.append(self.immutablevalue(getattr(c, attr)))

        if change:
            for position in access.read_locations:
                self.annotator.reflowfromposition(position)
                
        return unionof(*actuals)        

    def pycall(self, func, args):
        if func is None:   # consider None as a NULL function pointer
            return SomeImpossibleValue()
        if isinstance(func, (type, ClassType)) and \
            func.__module__ != '__builtin__':
            cls = func
            specialize = getattr(cls, "_specialize_", False)
            if specialize:
                if specialize == "location":
                    cls = self.specialize_by_key(cls, self.position_key)
                else:
                    raise Exception, \
                          "unsupported specialization type '%s'"%(specialize,)

            classdef = self.getclassdef(cls)
            s_instance = SomeInstance(classdef)
            # flow into __init__() if the class has got one
            init = getattr(cls, '__init__', None)
            if init is not None and init != object.__init__:
                # don't record the access of __init__ on the classdef
                # because it is not a dynamic attribute look-up, but
                # merely a static function call
                if hasattr(init, 'im_func'):
                    init = init.im_func
                else:
                    assert isinstance(init, BuiltinMethodType)
                s_init = self.immutablevalue(init)
                s_init.call(args.prepend(s_instance))
            else:
                try:
                    args.fixedunpack(0)
                except ValueError:
                    raise Exception, "no __init__ found in %r" % (cls,)
            return s_instance
        if hasattr(func, '__call__') and \
           isinstance(func.__call__, MethodType):
            func = func.__call__
        if hasattr(func, 'im_func'):
            if func.im_self is not None:
                s_self = self.immutablevalue(func.im_self)
                args = args.prepend(s_self)
            # for debugging only, but useful to keep anyway:
            try:
                func.im_func.class_ = func.im_class
            except AttributeError:
                # probably a builtin function, we don't care to preserve
                # class information then
                pass
            func = func.im_func
        assert isinstance(func, FunctionType), "[%s] expected function, got %r" % (self.whereami(), func)
        # do we need to specialize this function in several versions?
        specialize = getattr(func, '_specialize_', False)

        if specialize:
            base_func = func
            if specialize == 'argtypes':
                key = short_type_name(args)
                func = self.specialize_by_key(func, key,
                                              func.__name__+'__'+key)
            elif specialize == "location":
                # fully specialize: create one version per call position
                func = self.specialize_by_key(func, self.position_key)
            elif specialize == "memo":
                # call the function now, and collect possible results
                arglist_s, kwds_s = args.unpack()
                assert not kwds_s, ("no ** args in call to function "
                                    "marked specialize='concrete'")
                possible_results = []
                for arglist in possible_arguments(arglist_s):
                    result = func(*arglist)
                    possible_results.append(self.immutablevalue(result))
                return unionof(*possible_results)
            else:
                raise Exception, "unsupported specialization type '%s'"%(specialize,)

        elif func.func_code.co_flags & CO_VARARGS:
            # calls to *arg functions: create one version per number of args
            assert not args.has_keywords(), (
                "keyword forbidden in calls to *arg functions")
            nbargs = len(args.arguments_w)
            if args.w_stararg is not None:
                s_len = args.w_stararg.len()
                assert s_len.is_constant(), "calls require known number of args"
                nbargs += s_len.const
            func = self.specialize_by_key(func, nbargs,
                                          name='%s__%d' % (func.func_name,
                                                           nbargs))

        # parse the arguments according to the function we are calling
        signature = cpython_code_signature(func.func_code)
        defs_s = []
        if func.func_defaults:
            for x in func.func_defaults:
                defs_s.append(self.immutablevalue(x))
        try:
            inputcells = args.match_signature(signature, defs_s)
        except ArgErr, e:
            assert False, 'ABOUT TO IGNORE %r' % e     # we should take care that we don't end up here anymore
            return SomeImpossibleValue()

        r = self.annotator.recursivecall(func, self.position_key, inputcells)

        # in the case of argtypes specialisation we may have been calling a
        # different function for the site which could also be just partially analysed,
        # we need to force unifying all past and present results for the site
        # in order to guarantee the more general results invariant.
        if specialize == 'argtypes':
            key = (base_func, self.position_key)
            prev_r = self.argtypes_spec_callsite_results.get(key)
            if prev_r is not None:
                r = unionof(prev_r, r)
            self.argtypes_spec_callsite_results[key] = r
        return r
        
        

    def whereami(self):
        return self.annotator.whereami(self.position_key)

    def warning(self, msg):
        try:
            pos = self.whereami()
        except AttributeError:
            pos = '?'
        ansi_print("*** WARNING: [%s] %s" % (pos, msg), esc="31") # RED

    def specialize_by_key(self, thing, key, name=None):
        key = thing, key
        try:
            thing = self.cachespecializations[key]
        except KeyError:
            if isinstance(thing, FunctionType):
                # XXX XXX XXX HAAAAAAAAAAAACK
                # xxx we need a way to let know subsequent phases (the generator) about the specialized function
                # the caller flowgraph as it is doesn't.
                # This line just avoids that the flowgraph of the original function, which is what will be considered
                # and compiled for now will be computed during generation itself
                self.annotator.translator.getflowgraph(thing)
                #
                thing = func_with_new_name(thing, name or thing.func_name)
            elif isinstance(thing, (type, ClassType)):
                superclasses = iter(inspect.getmro(thing))
                superclasses.next() # skip thing itself
                for cls in superclasses:
                    assert not hasattr(cls, "_specialize_"), "for now specialization only for leaf classes"
                
                newdict = {}
                for attrname,val in thing.__dict__.iteritems():
                    if attrname == '_specialize_': # don't copy the marker
                        continue
                    if isinstance(val, FunctionType):
                        fname = val.func_name
                        if name:
                            fname = "%s_for_%s" % (fname, name)
                        newval = func_with_new_name(val, fname)
                    # xxx more special cases
                    else: 
                        newval  = val
                    newdict[attrname] = newval

                thing = type(thing)(name or thing.__name__, (thing,), newdict)
            else:
                raise Exception, "specializing %r?? why??"%thing
            self.cachespecializations[key] = thing
        return thing


def getbookkeeper():
    """Get the current Bookkeeper.
    Only works during the analysis of an operation."""
    try:
        return TLS.bookkeeper
    except AttributeError:
        return None

def ishashable(x):
    try:
        hash(x)
    except TypeError:
        return False
    else:
        return True

def short_type_name(args):
    l = []
    shape, args_w = args.flatten()
    for x in args_w:
        if isinstance(x, SomeInstance) and hasattr(x, 'knowntype'):
            name = "SI_" + x.knowntype.__name__
        else:
            name = x.__class__.__name__
        l.append(name)
    return "__".join(l)

def possible_arguments(args):
    # enumerate all tuples (x1,..xn) of concrete values that are contained
    # in a tuple args=(s1,..sn) of SomeXxx.  Requires that each s be either
    # a constant or SomePBC.
    if not args:
        yield ()
        return
    s = args[0]
    if s.is_constant():
        possible_values = [s.const]
    elif isinstance(s, SomePBC):
        for value in s.prebuiltinstances.values():
            assert value is True, ("concrete call with a method bound "
                                   "on a non-constant instance")
        possible_values = s.prebuiltinstances.keys()
    else:
        raise AssertionError, "concrete call with a non-constant arg %r" % (s,)
    for tuple_tail in possible_arguments(args[1:]):
        for value in possible_values:
            yield (value,) + tuple_tail
