"""Local preparation retains source-grounded context without model API calls."""

import json
import stat

import pytest

from gamescoach import knowledge
from gamescoach.knowledge import Library


@pytest.fixture
def saved_library(tmp_path):
    """Use the original index format to exercise upgrading existing libraries."""
    root = tmp_path / 'library'
    root.mkdir(mode=0o700)
    guides = [
        {
            'id': 'a' * 16,
            'filename': 'Methodology.pdf',
            'pages': 4,
            'passages': [
                {'page': 1, 'text': 'Mechanics and consistency precede intensity in functional movement methodology.'},
                {'page': 2, 'text': 'Programming balances progression and a varied training stimulus.'},
                {'page': 3, 'text': 'Aerobic endurance and conditioning use repeatable sustainable intervals.'},
                {'page': 4, 'text': 'Strength, weightlifting technique, and gymnastics skills need regular practice.'},
            ],
        },
        {
            'id': 'b' * 16,
            'filename': 'Competitors.pdf',
            'pages': 2,
            'passages': [
                {'page': 1, 'text': 'Competition specificity addresses weaknesses; taper volume before testing.'},
                {'page': 2, 'text': 'Recovery relies on sleep and nutrition, with carbohydrate fuel and protein.'},
            ],
        },
    ]
    for guide in guides:
        (root / f'{guide["id"]}.json').write_text(json.dumps(guide), encoding='utf-8')
    return Library(root), guides


def test_startup_prepares_legacy_library_and_brief_covers_source_themes(saved_library):
    library, guides = saved_library
    assert library.prepare() == {'guides': 2, 'pages': 6, 'brief_passages': 6}
    brief = library.brief()
    assert {entry['guide_id'] for entry in brief} == {guide['id'] for guide in guides}
    combined = ' '.join(entry['text'] for entry in brief).lower()
    for term in ('mechanics', 'programming', 'aerobic', 'strength', 'competition', 'recovery'):
        assert term in combined
    for entry in brief:
        guide = next(guide for guide in guides if guide['id'] == entry['guide_id'])
        assert any(passage['page'] == entry['pdf_page'] and entry['text'] in passage['text'] for passage in guide['passages'])
    path = library.root / '.cache' / 'prepared.json'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert len(list(library.root.glob('*.json'))) == 2


def test_prepared_library_reuses_index_across_queries_and_instances(saved_library, monkeypatch):
    library, _ = saved_library
    library.prepare()
    tokenized = []
    real_terms = knowledge._terms

    def record_terms(text):
        tokenized.append(text)
        return real_terms(text)

    def unexpected_source_read(self):
        pytest.fail('A warm query reread source JSON indexes')

    monkeypatch.setattr(knowledge, '_terms', record_terms)
    monkeypatch.setattr(Library, 'list_guides', unexpected_source_read)
    assert library.select('aerobic intervals')
    assert Library(library.root).select('competition taper')
    assert Library(library.root).brief()
    assert Library(library.root).prepare()['pages'] == 6
    assert tokenized == ['aerobic intervals', 'competition taper']


def test_new_process_cache_reload_does_not_retokenize_sources(saved_library, monkeypatch):
    library, _ = saved_library
    expected = library.brief()
    knowledge._PREPARED.clear()  # Simulate a fresh CLI process.

    def unexpected(*args):
        pytest.fail('The persistent prepared cache was not used')

    monkeypatch.setattr(knowledge, '_terms', unexpected)
    monkeypatch.setattr(Library, 'list_guides', unexpected)
    reloaded = Library(library.root)
    assert reloaded.prepare()['guides'] == 2
    assert reloaded.brief() == expected
    # Mutable response objects cannot poison the cached knowledge.
    reloaded.brief()[0]['text'] = 'accidental caller edit'
    assert reloaded.brief() == expected


@pytest.mark.parametrize('damage', ['json', 'version', 'citation', 'tokens'])
def test_corrupt_or_obsolete_prepared_cache_rebuilds_from_imports(saved_library, damage):
    library, _ = saved_library
    expected = library.brief()
    path = library.root / '.cache' / 'prepared.json'
    data = json.loads(path.read_text())
    if damage == 'json':
        path.write_text('{interrupted')
    else:
        if damage == 'version':
            data['version'] = 0
        elif damage == 'citation':
            data['brief'][0]['text'] = 'Fabricated passage that was never in the source'
        else:
            data['words']['a' * 16][0] = ['inventedtoken']
        path.write_text(json.dumps(data))
    knowledge._PREPARED.clear()
    assert Library(library.root).brief() == expected
    assert json.loads(path.read_text())['version'] == knowledge.CACHE_VERSION


def test_source_edit_and_remove_invalidate_warmed_and_persisted_context(saved_library):
    library, guides = saved_library
    assert 'sleep' in ' '.join(entry['text'] for entry in library.brief())
    changed = guides[1]
    changed['passages'][1]['text'] = 'Recovery requires planned rest and fueling.'
    path = library.root / f'{changed["id"]}.json'
    path.write_text(json.dumps(changed))
    assert 'sleep' not in ' '.join(entry['text'] for entry in Library(library.root).brief())
    assert any('planned rest' in entry['text'] for entry in library.select('recovery'))
    library.remove(changed['id'])
    assert library.prepare()['guides'] == 1
    assert {entry['guide_id'] for entry in library.brief()} == {guides[0]['id']}
    knowledge._PREPARED.clear()
    assert Library(library.root).prepare()['guides'] == 1


def test_new_guide_is_automatically_included_after_cache_preparation(saved_library):
    library, _ = saved_library
    library.prepare()
    guide = {'id': 'c' * 16, 'filename': 'Added.pdf', 'pages': 1, 'passages': [{'page': 1, 'text': 'CrossFit technique and recovery support adaptation.'}]}
    (library.root / f'{guide["id"]}.json').write_text(json.dumps(guide))
    assert Library(library.root).prepare()['guides'] == 3
    assert 'c' * 16 in {entry['guide_id'] for entry in library.brief()}


def test_foundation_budget_and_every_guide_representation(tmp_path):
    root = tmp_path / 'library'
    root.mkdir()
    for number in range(6):
        identifier = str(number) * 16
        guide = {
            'id': identifier,
            'filename': ('Guide' + str(number) + 'x' * 245 + '.pdf'),
            'pages': 6,
            'passages': [{'page': page + 1, 'text': (text + ' Source material. ' * 100)[:knowledge.PASSAGE_CHARS]} for page, text in enumerate([
                'Mechanics consistency intensity methodology.', 'Programming progression stimulus.', 'Aerobic endurance conditioning.',
                'Strength weightlifting skills.', 'Competition taper specificity.', 'Recovery nutrition sleep.',
            ])],
        }
        assert len(guide['filename']) <= 255
        (root / f'{identifier}.json').write_text(json.dumps(guide))
    library = Library(root)
    brief = library.brief()
    assert 6 <= len(brief) <= 8
    assert len({entry['guide_id'] for entry in brief}) == 6
    assert all(len(entry['text']) <= 900 for entry in brief)
    assert len(json.dumps(brief, ensure_ascii=False)) <= 8000
    knowledge._PREPARED.clear()
    assert Library(root).brief() == brief


def test_empty_library_preparation_is_free_of_filesystem_mutations(tmp_path):
    root = tmp_path / 'does-not-exist'
    library = Library(root)
    assert library.prepare() == {'guides': 0, 'pages': 0, 'brief_passages': 0}
    assert library.brief() == []
    assert library.select('aerobic') == []
    assert not root.exists()


def test_escaped_brief_text_cannot_stall_preparation_or_omit_guides(tmp_path):
    root = tmp_path / 'library'
    root.mkdir()
    for number in range(6):
        guide = {
            'id': str(number) * 16,
            'filename': 'f' * 150,
            'pages': 2,
            'passages': [{'page': 1, 'text': 'methodology programming aerobic strength competition ' + 'x' * 760}],
        }
        if number == 0:
            guide['passages'].append({'page': 2, 'text': 'recovery ' + '"' * 445})
        (root / f'{guide["id"]}.json').write_text(json.dumps(guide))
    brief = Library(root).brief()
    assert len({entry['guide_id'] for entry in brief}) == 6
    assert len(json.dumps(brief, ensure_ascii=False)) <= knowledge.MAX_BRIEF_CHARS


def test_unusually_escaped_filename_fails_actionably_instead_of_stalling(tmp_path):
    root = tmp_path / 'library'
    root.mkdir()
    for number in range(6):
        guide = {'id': str(number) * 16, 'filename': '\u0001' * 255, 'pages': 1, 'passages': [{'page': 1, 'text': 'Mechanics consistency intensity.'}]}
        (root / f'{guide["id"]}.json').write_text(json.dumps(guide))
    with pytest.raises(knowledge.KnowledgeError, match='filename'):
        Library(root).prepare()


def test_removing_last_guide_drops_derived_disk_copy(saved_library):
    library, guides = saved_library
    library.prepare()
    cache = library.root / '.cache' / 'prepared.json'
    assert cache.exists()
    for guide in guides:
        library.remove(guide['id'])
    assert not cache.exists()
    assert library.prepare() == {'guides': 0, 'pages': 0, 'brief_passages': 0}
