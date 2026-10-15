import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "factory/web_ui/static/app.js"
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


def function_source(name):
    source = APP.read_text()
    match = re.search(r"^function " + name + r"\(.*?(?=^function |^async function |^const |\Z)", source, re.M | re.S)
    if not match:
        raise AssertionError(name)
    return match.group(0)


class SessionWebMemoryTest(unittest.TestCase):
    @unittest.skipUnless(CHROME, "Chrome unavailable")
    def test_import_pairing_and_compaction_cards_in_real_dom(self):
        functions = "\n".join(function_source(name) for name in (
            "sessionMessageItems", "compactionItem", "renderCompaction",
            "registerTimelineUpdater",
            "externalToolParts", "externalToolInput",
        ))
        script = r'''
const timelineUpdaters = new WeakMap();
const t = key => key;
const element = (tag, cls, text) => { const node = document.createElement(tag); node.className = cls; if (text !== undefined) node.textContent = text; return node; };
window.IgorekMarkdown = { renderMarkdown(text, node) { node.textContent = text; } };
const messages = [
 {role:"user", text:"old question", history_id:"u"},
 {role:"tool", kind:"tool_call", tool_use_id:"a", tool_name:"Bash", tool_input:{command:"pytest"}, history_id:"a"},
 {role:"tool", kind:"tool_call", tool_use_id:"b", tool_name:"Read", history_id:"b"},
 {role:"tool", kind:"tool_result", tool_use_id:"b", tool_output:"file", history_id:"br"},
 {role:"tool", kind:"tool_result", tool_use_id:"a", tool_output:"3 failed", history_id:"ar"},
 {role:"assistant", text:"old answer", history_id:"end"}
];
const items = sessionMessageItems(messages);
const card = renderCompaction(compactionItem({kind:"compaction.completed", task_id:"t", payload:{compaction_id:"c", summary_text:"## Цель\n<img src=x onerror=alert(1)>", covered_history_ids:["u"]}}));
document.body.append(card);
const progress = renderCompaction(compactionItem({kind:"compaction.progress", payload:{compaction_id:"c", summary_text:"memory",chunk:1,total_chunks:2}}));
document.body.append(progress);
document.body.dataset.result = JSON.stringify({
 count:items.length, pairedA:items[1].result, pairedB:items[2].result, input:items[1].input.command,
 summary:card.textContent, details:card.tagName, progressOpen:progress.open, images:card.querySelectorAll("img").length
});
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "verify.html"
            path.write_text("<!doctype html><body><script>" + functions + script + "</script>")
            result = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--user-data-dir=" + directory + "/profile", "--dump-dom", path.as_uri()], capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        import html
        match = re.search(r'data-result="([^"]+)"', result.stdout)
        self.assertIsNotNone(match, result.stdout[-1500:])
        values = json.loads(html.unescape(match.group(1)))
        self.assertEqual(values["count"], 4)
        self.assertEqual(values["pairedA"], "3 failed")
        self.assertEqual(values["pairedB"], "file")
        self.assertEqual(values["input"], "pytest")
        self.assertEqual(values["details"], "DETAILS")
        self.assertTrue(values["progressOpen"])
        self.assertEqual(values["images"], 0)
        self.assertIn("## Цель", values["summary"])
