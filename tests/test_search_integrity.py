import copy
import json
import pytest
from jtorrent_backend.search_integrity import required_scope, validate_scopes

@pytest.fixture
def contract(tmp_path):
    prefix = 'data/v4/releases/v4-test/'
    docs = [{'doc_id': 0, 'category': 'linux', 'source_id': 'ubuntu', 'policy_scope': 'open-authorized'},
            {'doc_id': 1, 'category': 'linux', 'source_id': 'the_pirate_bay', 'policy_scope': 'third-party-unverified'}]
    counts = {'open-authorized': 1, 'third-party-unverified': 1}
    manifest = {'release_id': 'v4-test', 'item_count': 2, 'doc_count': 2, 'counts_by_scope': counts,
                'categories': ['linux'], 'sources': ['ubuntu', 'the_pirate_bay'], 'scopes': list(counts),
                'doc_shards': [{'path': prefix+'docs.json', 'count': 2, 'start_doc_id': 0, 'end_doc_id': 1}],
                'token_buckets': [{'path': prefix+'tokens.json'}]}
    root = {'item_count': 2, 'counts_by_scope': counts.copy()}
    status = copy.deepcopy(root)
    for name, data in [('docs', docs), ('tokens', {'tokens': {'linux': {'count': 2, 'postings': [[0,0,0,0,8], [1,0,1,1,7]]}}})]:
        target = tmp_path / (prefix+name+'.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding='utf-8')
    return tmp_path, manifest, root, status

def test_valid_contract(contract):
    validate_scopes(*contract, {'the_pirate_bay': 'third-party-unverified'})

@pytest.mark.parametrize('value', [None, '', 'unknown', 'authorized', 0])
def test_missing_scope_never_defaults_open(value):
    with pytest.raises(ValueError):
        required_scope(value)

@pytest.mark.parametrize('fault', ['manifest', 'counts', 'missing', 'posting', 'source', 'range', 'path'])
def test_rejects_corrupt_release(contract, fault):
    public, manifest, root, status = contract
    docs_path = public / manifest['doc_shards'][0]['path']
    if fault == 'manifest':
        manifest['scopes'] = ['open-authorized']
    elif fault == 'counts':
        status['counts_by_scope'] = {'open-authorized': 2}
    elif fault in {'missing', 'source'}:
        docs = json.loads(docs_path.read_text())
        docs[1]['policy_scope'] = None if fault == 'missing' else 'open-authorized'
        docs_path.write_text(json.dumps(docs))
    elif fault == 'posting':
        (public / manifest['token_buckets'][0]['path']).write_text(json.dumps({'tokens': {'linux': {'count': 1, 'postings': [[1,0,1,0,7]]}}}))
    elif fault == 'range':
        manifest['doc_shards'][0]['end_doc_id'] = 99
    elif fault == 'path':
        manifest['doc_shards'][0]['path'] = '../outside.json'
    with pytest.raises(ValueError):
        validate_scopes(*contract, {'the_pirate_bay': 'third-party-unverified'})
