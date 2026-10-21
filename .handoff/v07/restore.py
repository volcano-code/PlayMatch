import hashlib, lzma, os, subprocess
from pathlib import Path
base = lzma.decompress(b''.join(Path(f'.handoff/v07/{i:02}.part').read_bytes() for i in range(5)))
assert hashlib.sha256(base).hexdigest() == '14de162b4b7e868a4dd6e6cbe991173bd8723bd957aa3e3fd477791efe686137'
fix = Path('.handoff/v07/browser-fix.patch').read_bytes()
directory = Path(os.environ['RUNNER_TEMP']) / 'v07-evidence'
directory.mkdir(exist_ok=True)
(directory / 'base.patch').write_bytes(base)
(directory / 'browser-fix.patch').write_bytes(fix)
subprocess.run(['git', 'reset', '--hard', '4f61b364fe9bd11282a407449137524d8edecef6'], check=True)
for name in ('base.patch', 'browser-fix.patch'):
    subprocess.run(['git', 'apply', '--index', str(directory / name)], check=True)
tree = subprocess.check_output(['git', 'write-tree']).decode().strip()
assert tree == '1841d151b8b95e1a4e873a78e97bc1007a43eddf', tree
(directory / 'candidate.patch').write_bytes(subprocess.check_output(['git', 'diff', '--cached', '--binary']))
print('Verified final candidate', tree)
