#!/usr/bin/env python3
"""Build a relocatable /code ZIP inside Debian 12. Never include environment files."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

code = Path('/code')
libs = code / 'lib'; libs.mkdir(exist_ok=True)
shutil.copy2('/etc/ssl/certs/ca-certificates.crt', code / 'ca-certificates.crt')
# Resolve every ELF dependency, including Python wheels and Chromium, in the build OS.
for folder in [code, Path('/usr/sbin/nginx'), Path('/usr/bin/envsubst')]:
    files = folder.rglob('*') if folder.is_dir() else [folder]
    for file in list(files):
        if not file.is_file() or libs in file.parents: continue
        with file.open('rb') as source:
            if source.read(4) != b'\x7fELF': continue
        result = subprocess.run(['ldd', str(file)], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if 'not found' in line: raise RuntimeError('Unresolved native dependency: ' + str(file))
            for part in line.split():
                if part.startswith('/') and Path(part).is_file():
                    target = libs / Path(part).name
                    if not target.exists(): shutil.copy2(part, target)
# Dynamic NSS modules and font data used by headless Chromium.
if (code / 'browsers').exists():
    for pattern in ('libnss*.so', 'libsoftokn*.so', 'libfreebl*.so'):
        for file in Path('/usr/lib/x86_64-linux-gnu').glob(pattern): shutil.copy2(file, libs / file.name)
    shutil.copytree('/usr/share/fonts', code / 'fonts', dirs_exist_ok=True)
    (code / 'fonts.conf').write_text('<fontconfig><dir>/code/fonts</dir><cachedir>/tmp/font-cache</cachedir></fontconfig>')
for file in list(code.rglob('__pycache__')):
    if file.is_dir(): shutil.rmtree(file)
output = Path('/out'); output.mkdir(exist_ok=True)
archive = output / (sys.argv[1] + '.zip')
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for directory, _, files in os.walk(code, followlinks=True):
        for name in sorted(files):
            file = Path(directory) / name
            if name.startswith('.env'): raise RuntimeError('Refusing possible credentials in package')
            if file.is_file(): z.write(file, file.relative_to(code))
if archive.stat().st_size > 500 * 1024 * 1024: raise RuntimeError('Package exceeds Shenzhen FC 500 MiB limit; split dependencies into layers')
print(f'{archive.name}: {archive.stat().st_size} bytes')
