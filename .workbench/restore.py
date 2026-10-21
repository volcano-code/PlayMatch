import base64
import hashlib
import lzma
import os
import subprocess
from pathlib import Path

out = Path(os.environ['RUNNER_TEMP']) / 'v04-evidence'
out.mkdir(exist_ok=True)
encoded = ''.join(Path(f'.workbench/v04-part-{i}.txt').read_text() for i in range(4))
patch = lzma.decompress(base64.b64decode(encoded, validate=True))
assert hashlib.sha256(patch).hexdigest() == '7b750cb1649006a487b5a4c4c8e72cf55f7b613468dbb8a31f3cdd5621cbed40'
fix = lzma.decompress(base64.b64decode(Path('.workbench/v04-hardening.b64').read_text(), validate=True))
assert hashlib.sha256(fix).hexdigest() == '7f4da115b360ba055a89126baba5709f7ff890709187f0690e8f1d9e75c8036c'
(out / 'base.patch').write_bytes(patch)
(out / 'hardening.patch').write_bytes(fix)
subprocess.run(['git', 'read-tree', '--reset', '-u', os.environ['BASE_COMMIT']], check=True)
for name in ('base.patch', 'hardening.patch'):
    subprocess.run(['git', 'apply', '--index', '--whitespace=error', str(out / name)], check=True)
tree = subprocess.check_output(['git', 'write-tree']).decode().strip()
assert tree == os.environ['EXPECTED_TREE'], tree
(out / 'candidate.patch').write_bytes(subprocess.check_output(['git', 'diff', '--cached', os.environ['BASE_COMMIT'], '--binary']))
print('CANDIDATE_TREE ' + tree)
