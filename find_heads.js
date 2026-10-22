const fs = require('fs');
const path = require('path');

const dir = path.join(__dirname, 'api/alembic/versions');
const files = fs.readdirSync(dir).filter(f => f.endsWith('.py'));

const revisions = new Map();
const downRevs = new Set();

for (const file of files) {
  const content = fs.readFileSync(path.join(dir, file), 'utf8');
  const revMatch = content.match(/revision(?:\s*:\s*[^=\n]+)?\s*=\s*['"]([^'"]+)['"]/);
  const downMatch = content.match(/down_revision(?:\s*:\s*[^=\n]+)?\s*=\s*(?:['"]([^'"]+)['"]|\(([^)]+)\)|\[([^\]]+)\]|None)/);

  if (revMatch) {
    const rev = revMatch[1];
    revisions.set(rev, file);
    if (downMatch) {
      if (downMatch[1]) {
        downRevs.add(downMatch[1]);
      } else if (downMatch[2] || downMatch[3]) {
        const matches = (downMatch[2] || downMatch[3]).match(/['"]([^'"]+)['"]/g);
        if (matches) {
          matches.forEach(m => downRevs.add(m.replace(/['"]/g, '')));
        }
      }
    }
  }
}

const heads = [];
for (const [rev, file] of revisions.entries()) {
  if (!downRevs.has(rev)) {
    heads.push({ rev, file });
  }
}

console.log('Heads found:', JSON.stringify(heads, null, 2));
