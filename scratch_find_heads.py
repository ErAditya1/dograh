import os
import re

dir_path = os.path.join(os.path.dirname(__file__), 'api', 'alembic', 'versions')
files = [f for f in os.listdir(dir_path) if f.endswith('.py')]

revisions = {}
down_revisions = set()
down_map = {}

for f in files:
    with open(os.path.join(dir_path, f), 'r', encoding='utf-8') as fp:
        content = fp.read()
    rev_m = re.search(r'revision(?:\s*:\s*[^=\n]+)?\s*=\s*[\'"]([^\'"]+)[\'"]', content)
    down_m = re.search(r'down_revision(?:\s*:\s*[^=\n]+)?\s*=\s*(?:[\'"]([^\'"]+)[\'"]|\(([^)]+)\)|\[([^\]]+)\]|None)', content)
    if rev_m:
        rev = rev_m.group(1)
        revisions[rev] = f
        if down_m:
            if down_m.group(1):
                down_revisions.add(down_m.group(1))
                down_map[rev] = [down_m.group(1)]
            elif down_m.group(2) or down_m.group(3):
                targets = re.findall(r'[\'"]([^\'"]+)[\'"]', down_m.group(2) or down_m.group(3))
                down_map[rev] = targets
                for t in targets:
                    down_revisions.add(t)
            else:
                down_map[rev] = []
        else:
            down_map[rev] = []

heads = [rev for rev in revisions if rev not in down_revisions]
print(f"Total revisions: {len(revisions)}")
print(f"Heads count: {len(heads)}")
for h in heads:
    print(f"HEAD: {h} in {revisions[h]}")
    # trace back 5 revisions
    curr = h
    chain = []
    for _ in range(5):
        downs = down_map.get(curr, [])
        if not downs:
            break
        chain.append(f"{downs} ({revisions.get(downs[0], 'unknown') if len(downs)==1 else 'multiple'})")
        curr = downs[0]
    print(f"   Ancestors: {' -> '.join(chain)}")
