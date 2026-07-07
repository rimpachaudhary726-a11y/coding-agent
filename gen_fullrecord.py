import subprocess, os

# Get all tracked files
def get_tracked_files():
    result = subprocess.check_output(['git','ls-files']).decode().splitlines()
    return result

ignore_prefixes = ('artifacts/','node_modules/','lib/','.cache/')

files = [f for f in get_tracked_files() if not any(f.startswith(p) for p in ignore_prefixes) and f!='fullrecord_all.md' and f!='gen_fullrecord.py']

output_path = 'fullrecord_all.md'
with open(output_path,'w',encoding='utf-8') as out:
    out.write('# Full Record of Repository\n\n')
    out.write('This document lists every source file (excluding ignored directories) with its contents.\n\n')
    for f in files:
        out.write(f'## {f}\n')
        out.write('```\n')
        try:
            with open(f,'r',encoding='utf-8') as src:
                out.write(src.read())
        except Exception as e:
            out.write(f'Error reading file: {e}')
        out.write('\n```\n\n')
print('Generated', output_path)
