#!/usr/bin/env python3
"""CI validates extracted ZIPs in a clean Debian runtime, not the build image."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile
from configure import package_index

manifest=json.loads(Path('deploy/fc/functions.json').read_text())
package_index('fc-packages', manifest, {'RELEASE_SHA':'a'*40})
for f in manifest['functions']:
    with tempfile.TemporaryDirectory() as folder:
        with zipfile.ZipFile('fc-packages/'+f['name']+'.zip') as z:
            z.extractall(folder)
            for info in z.infolist():
                if not info.is_dir(): os.chmod(Path(folder)/info.filename, info.external_attr >> 16 & 0o777)
        command=['docker','run','--rm','--read-only','--tmpfs','/tmp:exec,size=1g','-v',folder+':/code:ro']
        if f['name'] == 'ai-engine':
            command += ['-e','PYTHONHOME=/code/python','-e','LD_LIBRARY_PATH=/code/lib:/code/python/lib','-e','PLAYWRIGHT_BROWSERS_PATH=/code/browsers','-e','FONTCONFIG_FILE=/code/fonts.conf','-e','HOME=/tmp','-e','PYTHONDONTWRITEBYTECODE=1','-w','/code','debian:bookworm-slim','/code/python/bin/python3','-c',
                "import fastapi, uvicorn, pymysql; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage']); page=b.new_page(); page.set_content('<p>FC ZIP</p>'); assert page.text_content('p')=='FC ZIP'; b.close(); p.stop()"]
        elif f['name'] in ('frontend','gateway','content'):
            command += ['-e','GATEWAY_MODE='+('content' if f['name']=='content' else 'app'),'-e','FC_INTERNAL_TOKEN='+'a'*64]
            for key in ('GAME_UPSTREAM','USER_UPSTREAM','AI_UPSTREAM','FRONTEND_UPSTREAM'): command += ['-e',key+'=https://example.com']
            command += ['debian:bookworm-slim','/code/bootstrap','-t']
        else:
            command += ['-e','LD_LIBRARY_PATH=/code/lib','-w','/code/packages/'+f['name'],'debian:bookworm-slim','/code/bin/node','-e',
                "require('@prisma/client'); require('@nestjs/core'); const fs=require('fs'); if(!fs.existsSync('dist/main.js')) process.exit(1);"]
        subprocess.run(command,check=True,timeout=180)
        print('Verified extracted package: '+f['name'])
