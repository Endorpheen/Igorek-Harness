"""Tree filtering keeps hierarchy and does not interpret file names as HTML."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_tree_filter_preserves_ancestors_and_file_statistics():
    node=shutil.which('node')
    if not node: pytest.skip('Node.js unavailable')
    module=Path(__file__).parents[1]/'factory/web_ui/static/changes.js'
    script=r'''
const assert = require('node:assert/strict');
const {filesTree} = require(process.argv[1]);
const files = [
 {path:'src/deep/a.py',status:'modified',additions:3,deletions:1},
 {path:'src/deep/b.py',status:'deleted',additions:0,deletions:2},
 {path:'<script>.py',status:'added',additions:1,deletions:0},
];
let tree=filesTree(files,'A.PY','');
assert.equal(tree.folders.size,1);
assert.equal(tree.folders.get('src').folders.get('deep').files[0].additions,3);
tree=filesTree(files,'src','deleted');
assert.deepEqual(tree.folders.get('src').folders.get('deep').files.map(f=>f.path),['src/deep/b.py']);
assert.equal(filesTree(files,'none','').folders.size,0);
assert.equal(filesTree(files,'<script>','').files[0].path,'<script>.py');
assert.equal(filesTree(files,'','').folders.get('src').folders.get('deep').files.length,2);
'''
    subprocess.run([node,'-e',script,str(module)],check=True,capture_output=True,text=True)


def test_statistics_explain_scope_partial_counts_and_exclusions():
    node=shutil.which('node')
    if not node: pytest.skip('Node.js unavailable')
    module=Path(__file__).parents[1]/'factory/web_ui/static/changes.js'
    script=r'''
const assert=require('node:assert/strict');
const {statistics}=require(process.argv[1]);
const data={scope:'tracked',totals:{files:7,counted_files:5,additions:10,deletions:3,unknown:2,partial:true,unavailable:{binary:1,too_large:1}},excluded_untracked:12000,omitted:4,omitted_by_reason:{untracked_limit:4}};
const ru=statistics(data,false),en=statistics(data,true);
assert.equal(ru.label,'Частичный итог: 7 файлов');
assert.ok(ru.tooltip.includes('Это не размер проекта'));
assert.ok(ru.tooltip.includes('Фильтр дерева не меняет общий итог'));
assert.ok(ru.notes.some(n=>n.includes('5 из 7')));
assert.ok(ru.notes.some(n=>n.includes('бинарные — 1')&&n.includes('размер файла — 1')));
assert.ok(ru.notes.some(n=>n.includes('не входят в итог: 4')));
assert.ok(ru.notes.some(n=>n.includes('не включены: 12000')));
assert.ok(en.label.startsWith('Partial total:'));
assert.ok(en.notes.some(n=>n.includes('binary — 1')));
assert.equal(statistics({scope:'tracked',totals:{files:2,unknown:0,partial:false}},false).label,'Изменено файлов: 2');
assert.ok(statistics({scope:'session_files',totals:{}},false).scope.includes('записанными правками'));
'''
    subprocess.run([node,'-e',script,str(module)],check=True,capture_output=True,text=True)
