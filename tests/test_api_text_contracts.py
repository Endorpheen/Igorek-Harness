"""Exercise API text helpers with protocol-shaped inputs and no tool execution."""
import pytest

from factory.api_server_core import text_utils as tu


@pytest.mark.parametrize('text,expected',[
    ('',[]),('Please inspect these notes',[]),
    ('Inspect `src/main.py` and `tests/test_main.py`.',['src/main.py','tests/test_main.py']),
    ('Read "notes.md" and \'data.json\'.',['notes.md','data.json']),
    ('Read `./notes.md`, `../data/info.json` and `/fixture/main.py`.',['./notes.md','../data/info.json','/fixture/main.py']),
    ('Use `notes.md` then notes.md and `notes.md`.',['notes.md']),
    ('Read `A.txt` and `a.txt`.',['A.txt','a.txt']),
    ('Versions 3.14 and 0.5; visit example.com and example.ru.',[]),
    ('document.querySelector window.location console.log process.env module.exports this.method',[]),
    ('https://example.com/docs/guide.md https://example.org/data.json',[]),
    ('Use `archive.tar.gz` for the fixture.',['archive.tar.gz']),
])
def test_filename_candidates_keep_real_file_hints_without_promoting_urls_or_properties(text,expected):
    assert tu.find_explicit_filenames(text)==expected
    assert tu.find_explicit_filename(text)==(expected[0] if expected else None)


@pytest.mark.parametrize('text,candidate,expected',[
    ('notes.md','notes.md',False),('3.14','3.14',True),('123.456','123.456',True),
    ('noextension','noextension',True),('.md','.md',True),('notes.123','notes.123',True),
    ('xnotes.mdy','notes.md',True),('_notes.md','notes.md',True),
    ('https://example.org/file.md','//example.org/file.md',True),
    ('/a/b/c/d/e/f/g/h/i/j/k/l/m/n.md','/a/b/c/d/e/f/g/h/i/j/k/l/m/n.md',True),
])
def test_filename_boundaries_reject_partial_tokens_and_non_file_shapes(text,candidate,expected):
    start=text.index(candidate)
    assert tu.should_skip_filename_candidate(text,start,start+len(candidate),candidate) is expected


@pytest.mark.parametrize('tool,expected',[
    ({},None),({'input_schema':None},None),({'input_schema':{'properties':[]}},None),
    ({'input_schema':{'properties':{'query':{}}}},None),
    ({'input_schema':{'properties':{'path':{}}}},'path'),
    ({'input_schema':{'properties':{'notebook_path':{}}}},'notebook_path'),
    ({'input_schema':{'properties':{'path':{},'file_path':{}}}},'file_path'),
])
def test_file_argument_uses_supported_schema_aliases_only(tool,expected):
    assert tu.resolve_file_arg_name(tool)==expected


@pytest.mark.parametrize('text,expected',[
    ('Nothing fenced here',None),
    ('Before\n```python\nprint("fixture")\n```\nAfter','print("fixture")'),
    ('```\n  fixture text  \n```','fixture text'),
    ('```c++\nint main() {}\n```','int main() {}'),
    ('```json\n{"ready":true}\n```\n```sh\nignored\n```','{"ready":true}'),
    ('```\n```',''),
    ('```python\r\nprint("fixture")\r\n```','print("fixture")'),
])
def test_code_block_extraction_preserves_the_first_body_across_line_endings(text,expected):
    assert tu.extract_code_block(text)==expected


@pytest.mark.parametrize('path,expected',[
    ('/fixture/AGENTS.md',True),('./CLAUDE.md',True),('AGENTS.md.md',True),
    ('/fixture/agents.MD',True),('/fixture/README.md',False),('AGENTS.md.backup',False),
])
def test_instruction_file_detection_matches_the_basename(path,expected):
    assert tu.is_protected_instruction_file(path) is expected


@pytest.mark.parametrize('text,expected',[
    ('  "fixture"  ','fixture'),(" 'Привет' ",'Привет'),(' `hello world` ','hello world'),
])
def test_request_token_cleanup_removes_only_surrounding_presentation(text,expected):
    assert tu.clean_request_token(text)==expected


@pytest.mark.parametrize('text,expected',[
    ('Создай notes.md с тремя строками:\nодин\n\nдва\nтри\nПотом прочитай его.','один\nдва\nтри'),
    ('Создай notes.md с содержимым Hello fixture\nЗатем проверь.','Hello fixture'),
    ('Создай notes.md с содержимым\n first  \n second \nПосле этого прочитай.','first\nsecond'),
    ('Другой запрос без явного содержимого',None),
    ('Создай notes.md с содержимым   ',None),
])
def test_explicit_creation_content_ends_before_followup_instructions(text,expected):
    assert tu.extract_creation_content_from_user_text(text,'notes.md')==expected


@pytest.mark.parametrize('command,expected',[
    ('',''),('   ',''),('\n',''),('\r\n',''),
    ('cat input.txt','input.txt'),('cat "my notes.md"','my notes.md'),
    ('echo fixture > result.txt','result.txt'),("echo fixture > 'my notes.md'",'my notes.md'),
    ('cat first.txt\ncat second.txt','first.txt'),('ls -la',''),
    ('cat input.txt | head',''),('echo fixture > result.txt; touch other',''),
])
def test_bash_target_extraction_handles_empty_and_bounded_commands(command,expected):
    assert tu.extract_bash_file_target(command)==expected


@pytest.mark.parametrize('text,expected',[
    ('Замени `old` на `new`',[('old','new')]),
    ('Замени old на new, замени alpha на beta',[('old','new'),('alpha','beta')]),
    ('значение 10 на 20',[('10','20')]),
    ('Замени old на new\nЗамени old на new',[('old','new')]),
    ('Замени same на same',[]),('Прочитай файл',[]),
])
def test_requested_replacements_are_nonempty_distinct_and_deduplicated(text,expected):
    assert tu.extract_requested_replacements(text)==expected


def test_read_prefix_removal_keeps_line_order_and_unprefixed_text():
    assert tu.strip_read_line_numbers(' 1→ first\n2→second\nplain')=='first\nsecond\nplain'


def test_import_summary_ignores_non_import_lines_and_preserves_first_appearance():
    text='1→import os\n2→from pathlib import Path\n3→print("import json")\n4→import os\n5→from .runtime import call'
    assert tu.extract_read_imports(text)==['os','pathlib','.runtime']


def test_import_summary_is_bounded_for_large_read_results():
    text='\n'.join(f'import package_{i}' for i in range(15))
    assert tu.extract_read_imports(text)==[f'package_{i}' for i in range(12)]


def test_constant_summary_excludes_nested_and_lowercase_assignments():
    text='1→MAX_RETRIES = 3\n2→    INNER_VALUE = 4\n3→setting = 5\n4→SERVICE_URL = "fixture"\n5→MAX_RETRIES = 7'
    assert tu.extract_top_level_constants(text)==['MAX_RETRIES','SERVICE_URL']


def test_constant_summary_is_bounded():
    text='\n'.join(f'CONSTANT_{i} = {i}' for i in range(15))
    assert tu.extract_top_level_constants(text)==[f'CONSTANT_{i}' for i in range(12)]


def test_decorated_entrypoints_include_routes_and_context_lifecycle_only():
    text='''1→@app.get("/health")
2→async def health():
3→    pass
4→@router.post("/messages")
5→@some_wrapper
6→def messages():
7→    pass
8→@dataclass
9→class Item:
10→    pass
11→@asynccontextmanager
12→async def lifespan():
13→    pass
14→@unrelated
15→def ordinary():
16→    pass'''
    assert tu.extract_decorated_entrypoints(text)==['health','messages','lifespan']


def test_decorated_summary_deduplicates_and_stays_bounded():
    block='@app.get("/fixture")\ndef route_0():\n    pass\n'
    text=block+block+'\n'.join(f'@router.get("/{i}")\ndef route_{i}():\n    pass' for i in range(1,13))
    assert tu.extract_decorated_entrypoints(text)==[f'route_{i}' for i in range(10)]


def test_main_entrypoint_excludes_nested_or_other_named_functions():
    text='1→def helper():\n2→    def main():\n3→        pass\n4→async def main():\n5→    pass\n6→def main():\n7→    pass'
    assert tu.extract_main_entrypoints(text)==['main']
    assert tu.extract_main_entrypoints('def helper():\n    pass')==[]


@pytest.mark.parametrize('limit',[0,1,4,5,6,10,25])
def test_section_sample_is_bounded_and_keeps_real_names_in_source_order(limit):
    names=[f'section_{i}' for i in range(20)]
    sample=tu.sample_section_names(names,limit=limit)
    assert len(sample)==min(limit,len(names))
    assert len(sample)==len(set(sample))
    assert sample==sorted(sample,key=names.index)
    if limit:assert sample[0]==names[0]
    if limit>=8:assert sample[-3:]==names[-3:]


def test_small_section_list_is_kept_without_fabricated_entries():
    assert tu.sample_section_names([])==[]
    assert tu.sample_section_names(['alpha','beta'])==['alpha','beta']
