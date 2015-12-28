from fbuild.builders.cxx import guess_static, guess_shared
from fbuild.builders.c.msvc import Builder as MsvcBuilder
from fbuild.builders.c.gcc import Builder as GccBuilder
from fbuild.builders.pkg_config import PkgConfig
from fbuild.builders.c import Library
from fbuild.builders import find_program
from fbuild.record import Record
from fbuild.path import Path
import fbuild

import sys, re, textwrap

def check_fluid(linker):
    fluidsynth = Path(linker.prefix + 'fluidsynth' + linker.suffix)
    fluidsynth = fluidsynth.addroot(Path('fluidsynth') / 'fluidsynth' / 'src')

    message = textwrap.dedent('''
    You need to build Fluidsynth separately first!
    Try runnung 'cd fluidsynth/fluidsynth; cmake'.
    (See http://sourceforge.net/p/fluidsynth/wiki/BuildingWithCMake/ for info.)
    '''.rstrip().lstrip('\n')).replace('\n', ' ', 1)

    if not fluidsynth.exists():
        raise fbuild.ConfigFailed(message)

def get_libs_for(ctx, pkg, defaults):
    try:
        libs = PkgConfig(ctx, pkg).libs()
    except:
        ctx.logger.check('trying to get libs for %s' % pkg)
        libs = ' '.join(map(lib_arg.__add__, defaults[pkg]))
        ctx.logger.failed('failed (using defaults %s)' % libs)
    else:
        ctx.logger.check('trying to get libs for -%s' % pkg)
        ctx.logger.passed('ok %s' % libs)

    return libs

def write_fpc(ctx, fpc, write):
    directory = ctx.buildroot / 'config'
    directory.makedirs()
    ctx.logger.check(' * generating fpc', directory / fpc, color='yellow')
    write(directory)

@fbuild.db.caches
def gen_sfml_fpc(ctx, cxx):
    sys.path.insert(0, str(Path(__file__).dirname() / 'sfml'))
    import gen_fpc

    lib_arg = '' if isinstance(cxx, MsvcBuilder) else '-l'

    default_libs = {
        'system': ['system'],
        'window': ['window', 'system'],
        'graphics': ['graphics', 'window', 'system'],
    }

    all_libs = {}

    for pkg in gen_fpc.packages:
        all_libs[pkg] = get_libs_for(ctx, 'sfml-' + pkg, default_libs)

    for pkg, libs in all_libs.items():
        write_fpc(ctx, 'sfml-%s.fpc' % pkg, lambda d: gen_fpc.write(pkg, libs, d))
        # directory = ctx.buildroot / 'config'
        # directory.makedirs()
        # ctx.logger.check(' * generating fpc', directory / ('sfml-%s.fpc' % pkg),
                         # color='yellow')
        # gen_fpc.write(pkg, libs, str(directory))

def gen_fpc(*args):
    gen_sfml_fpc(*args)

class Felix(fbuild.db.PersistentObject):
    def __init__(self, ctx, flx=None, flx_pkgconfig=None):
        self.flx = find_program(ctx, [flx or 'flx'])
        self.ctx = ctx

        self._test()

    def _test(self):
        self.ctx.logger.check('checking flx')
        failed = False
        with self.tempfile("println 'Hello, world!';") as f:
            try:
                output = self.uncached_run(f, quieter=1)
            except:
                self.ctx.logger.failed()
                raise
            else:
                if output[1] or not output[0].endswith(b'Hello, world!\n'):
                    self.ctx.logger.failed()
                    raise fbuild.ConfigFailed(
                        'flx test program did not give correct output')
                else:
                    self.ctx.logger.passed()

    def uncached_compile(self, dst, src, cxx, includes=[], pkgconfig_paths=[],
                         libpaths=[], libs=[], cflags=[]):
        dst = Path(dst).addroot(self.ctx.buildroot)
        dst = dst.replaceext(cxx.exe_linker.suffix)

        new_libs = []

        def process_library(lib):
            if isinstance(lib, Library):
                for sublib in lib.libs:
                    process_library(sublib)

                new_libs.extend(lib.external_libs)
                libpaths.append(lib.dirname())
                slib = str(lib.basename().replaceext(''))
                if slib.startswith('lib') and lib.splitext()[1] == '.a':
                    slib = slib[3:]
                new_libs.append(slib)
                libpaths.extend(lib.libpaths)

        for lib in libs:
            process_library(lib)

        cmd = [self.flx, '-c', '--static']
        cmd.extend('--pkgconfig-path+=' + path for path in pkgconfig_paths)
        cmd.extend('-I' + include for include in includes)
        cmd.extend('-L' + path for path in libpaths)
        cmd.extend('-l' + lib for lib in new_libs)
        cmd.extend('--cflags=' + flag for flag in cflags)
        cmd.extend(('-o', dst))
        cmd.append(src)

        self.ctx.execute(cmd, 'flx', '%s -> %s' % (src, dst), color='link')
        return dst

    @fbuild.db.cachemethod
    def compile(self, dst, src: fbuild.db.SRC, others: fbuild.db.SRCS, *args,
        **kw) -> fbuild.db.DST:
        return self.uncached_compile(dst, src, *args, **kw)

    def uncached_run(self, path, *args, **kw):
        return self.ctx.execute([self.flx, path], *args, **kw)

    def tempfile(self, code):
        return fbuild.temp.tempfile(code, '.flx')

    @fbuild.db.cacheproperty
    def platform_extra(self):
        # XXX
        return {'clang'}
        get_toolchain = '''
        include 'std/felix/toolchain_clang_config';
        include 'std/felix/toolchain_interface';
        include 'std/felix/flx_pkgconfig';

        config_dirs := #Config::std_config.FLX_CONFIG_DIRS;
        pkgconfig := FlxPkgConfig::FlxPkgConfigQuery config_dirs;
        toolchain := pkgconfig.getpkgfield1 ('toolchain', 'toolchain');

        for arg in #System::args perform
            if arg.startswith('--toolchain=') perform
                toolchain = arg.[12 to];

        println toolchain;
        '''

        with self.tempfile(get_toolchain) as f:
            self.ctx.logger.check('detecting toolchain used by flx '\
                                  '(this may take a while!)')
            try:
                toolchain = self.uncached_run(f, quieter=1)[0]
            except:
                self.ctx.logger.failed()
                raise fbuild.ConfigFailed('could not detect flx toolchain')
            else:
                toolchain = toolchain.decode('utf-8').strip().split('\n')[-1]
                self.ctx.logger.passed('ok %s' % toolchain)

        if 'msvc' in toolchain:
            return {'windows'}
        elif 'gcc' in toolchain:
            return {'gcc'}
        elif 'clang' in toolchain:
            return {'clang'}
        else:
            raise fbuild.ConfigFailed('unknown toolchain %s' % toolchain)

@fbuild.db.caches
def configure(ctx):
    felix = Felix(ctx)
    kw = dict(platform_extra=felix.platform_extra, platform_options=[
        ({'posix'}, {'flags+': ['-std=c++11']}),
    ])
    static = guess_static(ctx, **kw)
    shared = guess_shared(ctx, **kw)
    gen_fpc(ctx, static)
    check_fluid(shared.lib_linker)
    return Record(static=static, shared=shared, felix=felix)

#--------------------------------------------------------------------------------
# BUILDING.
#--------------------------------------------------------------------------------

def build_midifile(ctx, rec):
    return rec.static.build_lib('midifile',
        Path.glob('midifile/src-library/*.cpp'), includes=['midifile/include'])

def build_midifi(ctx, rec, midifile):
    all_sources = []
    for pat in 'midifi/*.flx', 'sfml/sfml/*.flx', 'midifile.flx/midifile.flx':
        all_sources.extend(Path.glob(pat))

    rec.felix.compile('midifi', 'midifi.flx', all_sources, rec.static,
                      includes=['sfml', 'midifile', 'midifile.flx'],
                      pkgconfig_paths=['build/config'], libs=[midifile])

def build(ctx):
    rec = configure(ctx)
    midifile = build_midifile(ctx, rec)
    build_midifi(ctx, rec, midifile)
