import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'html' / 'static' / 'broadcaster.js'

class QueueDropIndicatorTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is optional and is not installed')
    def test_canonical_drop_resolution_and_noop_suppression(self):
        source = JS.read_text()
        self.assertIn('function resolveQueueDropDestination', source)
        self.assertIn('if (insertIndex === fromIndex) return null;', source)
        self.assertIn('findQueueRow(destination.targetId)', source)

        match = re.search(r"  function resolveQueueDropDestination\(.*?\n  }\n\n  function findQueueRow", source, re.S)
        self.assertIsNotNone(match)
        function_source = match.group(0).rsplit('\n\n  function findQueueRow', 1)[0]
        script = function_source + r'''
const items = [{id:'A'},{id:'B'},{id:'C'},{id:'D'}];
function out(source,target,placement){
  const value = resolveQueueDropDestination(items, source, target, placement);
  return value ? [value.insertIndex, value.targetId, value.placement] : null;
}
const result = {
  duplicate1: out('D','A','after'),
  duplicate2: out('D','B','before'),
  noop1: out('B','A','after'),
  noop2: out('B','C','before'),
  end: out('A','D','after')
};
console.log(JSON.stringify(result));
'''
        completed = subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
        self.assertEqual(completed.stdout.strip(), '{"duplicate1":[1,"B","before"],"duplicate2":[1,"B","before"],"noop1":null,"noop2":null,"end":[3,"D","after"]}')

if __name__ == '__main__':
    unittest.main()
