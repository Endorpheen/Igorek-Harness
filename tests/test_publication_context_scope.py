"""An inference compressor must never carry summaries between conversations."""
import ast
import asyncio
from pathlib import Path
import types


def test_shared_compressor_uses_only_the_current_conversations_summary():
    source = Path(__file__).resolve().parents[1] / 'factory/api_server_core/context_compactor.py'
    tree = ast.parse(source.read_text())
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.ImportFrom) and node.module == 'paroquant.inference')]
    module = types.ModuleType('publication_context_fixture')
    import sys
    sys.modules[module.__name__] = module
    try:
        exec(compile(tree, str(source), 'exec'), module.__dict__)
        module.build_prompt = lambda tokenizer, messages, flag: str(messages)
        prompts = []

        class SummaryClient:
            async def generate_summary(self, prompt, **kwargs):
                prompts.append(prompt)
                return 'summary from conversation ' + str(len(prompts))

        compressor = module.ContextCompressor(model='fixture', tokenizer=None, context_length=2000,
            summary_client=SummaryClient(), config=module.ContextCompactorConfig(protect_first_n=0, protect_last_n=1))
        first = [{'role': 'user', 'content': 'private conversation A ' * 100} for _ in range(12)]
        second = [{'role': 'user', 'content': 'independent conversation B ' * 100} for _ in range(12)]
        _, status_a = asyncio.run(compressor.compress(first, current_tokens=2000))
        _, status_b = asyncio.run(compressor.compress(second, current_tokens=2000))
        assert status_a and status_b
        assert 'summary from conversation 1' not in prompts[1]
        continued = [{'role': 'assistant', 'content': module.SUMMARY_PREFIX + '\nexplicit summary for B'}, *second]
        asyncio.run(compressor.compress(continued, current_tokens=2000))
        assert 'explicit summary for B' in prompts[2]
        assert 'summary from conversation 1' not in prompts[2]
    finally:
        sys.modules.pop(module.__name__, None)
