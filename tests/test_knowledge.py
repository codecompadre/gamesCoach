from dataclasses import replace
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from gamescoach import knowledge, engine, chat
from gamescoach.knowledge import Library, KnowledgeError
from gamescoach.models import AthleteProfile, CoachingSession


def pdf_file(path, pages, password=None):
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        stream.set_data(f'BT /F1 12 Tf 50 700 Td ({escaped}) Tj ET'.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    writer.write(path)
    return path


@pytest.fixture(autouse=True)
def private_data(monkeypatch, tmp_path):
    monkeypatch.setenv('GAMESCOACH_DATA_DIR', str(tmp_path / 'data'))
    for name in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GAMESCOACH_OPENAI_API_KEY', 'GAMESCOACH_GEMINI_API_KEY', 'GAMESCOACH_COACH_NOTES_FILE'):
        monkeypatch.delenv(name, raising=False)


def test_two_pdfs_persist_and_each_is_represented_with_real_pdf_pages(tmp_path):
    one = pdf_file(tmp_path / 'Methodology.pdf', ['CrossFit methodology: mechanics consistency intensity.', 'Aerobic capacity uses sustainable repeatable conditioning.'])
    two = pdf_file(tmp_path / 'Competitors.pdf', ['Competition programming prioritizes weaknesses and recovery.', 'A pre competition taper reduces volume while preserving practice.'])
    library = Library()
    first, second = library.add(one), library.add(two)
    assert len(Library().list_guides()) == 2
    excerpts = Library().select('Improve aerobic capacity and prepare for competition taper')
    assert {item['guide_id'] for item in excerpts} == {first['id'], second['id']}
    assert any(item['pdf_page'] == 2 and 'Aerobic capacity' in item['text'] for item in excerpts)
    assert any(item['pdf_page'] == 2 and 'taper' in item['text'] for item in excerpts)
    assert str(tmp_path) not in json.dumps(excerpts)
    assert library.add(one)['id'] == first['id'] and len(library.list_guides()) == 2
    stored = library.root / (first['id'] + '.json')
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert stat.S_IMODE(library.root.stat().st_mode) == 0o700


def test_large_guides_are_chunked_and_prompt_budget_is_bounded(tmp_path):
    text = 'CrossFit aerobic conditioning programming mechanics consistency intensity recovery. ' * 200
    library = Library()
    guide = library.add(pdf_file(tmp_path / 'Long.pdf', [text, 'Strength specificity and competition taper.']))
    assert len(guide['passages']) > 8
    assert all(len(item['text']) <= knowledge.PASSAGE_CHARS for item in guide['passages'])
    selected = library.select('programming for aerobic conditioning')
    assert 1 <= len(selected) <= 8
    assert len(json.dumps(selected, ensure_ascii=False)) <= knowledge.MAX_CONTEXT_CHARS
    assert any(item['pdf_page'] == 2 for item in library.select('Explain PDF page 2'))


def test_remove_affects_future_lookup_but_preserves_original_pdf(tmp_path):
    path = pdf_file(tmp_path / 'Guide.pdf', ['CrossFit mechanics and consistency.'])
    before = path.read_bytes()
    library = Library()
    guide = library.add(path)
    library.remove(guide['id'])
    assert library.list_guides() == [] and library.select('CrossFit') == []
    assert path.read_bytes() == before
    with pytest.raises(KnowledgeError):
        library.remove('../outside')


def test_scan_encryption_and_invalid_input_are_actionable(tmp_path):
    library = Library()
    with pytest.raises(KnowledgeError, match='OCR'):
        library.add(pdf_file(tmp_path / 'scan.pdf', ['']))
    with pytest.raises(KnowledgeError, match='Password'):
        library.add(pdf_file(tmp_path / 'protected.pdf', ['CrossFit'], password='test-password'))
    path = tmp_path / 'broken.pdf'
    path.write_bytes(b'not a PDF')
    with pytest.raises(KnowledgeError, match='Cannot read'):
        library.add(path)
    with pytest.raises(KnowledgeError, match='Cannot open'):
        library.add(tmp_path / 'missing.pdf')
    with pytest.raises(KnowledgeError, match='.pdf'):
        library.add(tmp_path / 'word.docx')


def test_pdf_size_limits_and_corrupt_index_do_not_silently_pass(tmp_path, monkeypatch):
    path = pdf_file(tmp_path / 'Guide.pdf', ['CrossFit methodology'])
    library = Library()
    monkeypatch.setattr(knowledge, 'MAX_PDF_BYTES', 10)
    with pytest.raises(KnowledgeError, match='25 MB'):
        library.add(path)
    monkeypatch.setattr(knowledge, 'MAX_PDF_BYTES', 100000)
    library.add(path)
    next(library.root.glob('*.json')).write_text('{broken')
    with pytest.raises(KnowledgeError, match='Cannot read'):
        library.list_guides()


def mock_coaches(monkeypatch, module):
    calls = []
    for classname, name in (('OpenAIProvider', 'ChatGPT'), ('GeminiProvider', 'Gemini')):
        class Coach:
            def __init__(self, key, model, label=name):
                self.name, self.model = label, model
            def generate(self, *, instructions, prompt):
                calls.append((self.name, instructions, prompt))
                return 'Apply methodology to the CrossFit athlete.'
        monkeypatch.setattr(module, classname, Coach)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-openai')
    monkeypatch.setenv('GEMINI_API_KEY', 'test-gemini')
    return calls


@pytest.mark.parametrize('workflow', ['plan', 'chat'])
def test_both_coaches_receive_guidance_after_restart_and_even_without_recent_refs(tmp_path, monkeypatch, workflow):
    source = pdf_file(tmp_path / 'CrossFit Guide.pdf', ['Mechanics consistency intensity shape CrossFit methodology.', 'Aerobic endurance work is sustainable and repeatable.'])
    Library().add(source)
    module = engine if workflow == 'plan' else chat
    calls = mock_coaches(monkeypatch, module)
    if workflow == 'plan':
        result = engine.create_session('training', 'Improve aerobic capacity', AthleteProfile(), rounds=1)
    else:
        result = chat.ChatCoach(AthleteProfile(), []).reply('Improve aerobic capacity')
    assert [item[0] for item in calls] == ['ChatGPT', 'Gemini', 'ChatGPT']
    for _, instructions, prompt in calls:
        json_text = prompt.split('\n', 1)[1].split('\nTask focus:', 1)[0].split('\nYour turn:', 1)[0]
        data = json.loads(json_text)
        assert data['methodology_excerpts'] == result.methodology_excerpts
        assert any('Aerobic endurance' in item['text'] for item in data['methodology_excerpts'])
        assert 'PDF p. N' in instructions and 'not new system' in instructions
    restored = CoachingSession.from_dict(result.to_dict())
    assert restored.methodology_excerpts == result.methodology_excerpts
    if workflow == 'chat':
        coach = chat.ChatCoach(AthleteProfile(), [], session=restored)
        assert coach.reply('What about recovery?').methodology_excerpts
    assert 'test-openai' not in json.dumps(result.to_dict())


def test_demo_does_not_present_library_guidance_as_real_model_output(tmp_path):
    Library().add(pdf_file(tmp_path / 'Guide.pdf', ['CrossFit methodology']))
    result = engine.create_session('training', 'CrossFit', AthleteProfile(), demo=True)
    assert result.methodology_excerpts == []
    assert chat.ChatCoach(AthleteProfile(), [], demo=True).reply('CrossFit').methodology_excerpts == []


def run_cli(*args, input_text=None):
    return subprocess.run([sys.executable, '-m', 'gamescoach', *args], env=os.environ.copy(), input=input_text, text=True, capture_output=True, timeout=15)


def test_terminal_library_add_list_remove_and_chat_learn_prompt(tmp_path):
    one = pdf_file(tmp_path / 'Methodology.pdf', ['CrossFit mechanics consistency intensity.'])
    two = pdf_file(tmp_path / 'Competitors.pdf', ['Competition programming includes aerobic capacity and recovery.'])
    result = run_cli('library', 'add', str(one))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'without API calls' in result.stdout
    result = run_cli('chat', '--demo', input_text=f'/learn\n"{two}"\n/library\n/sources\n/quit\n')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Methodology.pdf' in result.stdout and 'Competitors.pdf' in result.stdout
    guides = Library().list_guides()
    assert len(guides) == 2
    assert run_cli('library', 'list').returncode == 0
    assert run_cli('library', 'remove', guides[0]['id']).returncode == 0
    assert len(Library().list_guides()) == 1


def test_menu_library_and_cancelled_learn_remain_usable(tmp_path):
    path = pdf_file(tmp_path / 'Guide.pdf', ['CrossFit methodology'])
    result = run_cli('--demo', input_text=f'10\n"{path}"\n8\n/learn\n/quit\n/library\n/quit\nq\n')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Library ready' in result.stdout and 'PDF import cancelled' in result.stdout
    assert 'Returning to the menu' in result.stdout
