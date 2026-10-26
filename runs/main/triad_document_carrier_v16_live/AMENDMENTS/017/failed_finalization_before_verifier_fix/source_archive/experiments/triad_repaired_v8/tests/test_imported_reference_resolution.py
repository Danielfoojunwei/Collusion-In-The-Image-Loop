"""Pure copied-only v7/v6 image-reference fixtures, never provider calls."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments.triad_repaired_v8.backend import TriadBackend
from experiments.triad_repaired_v8.imported_references import ContractError,canonical,sha


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);raw=(canonical(value)+"\n").encode();path.write_bytes(raw);return raw


class MultihopReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.out=self.root/'v8';self.v7=self.root/'v7';self.v6=self.root/'v6';self.out.mkdir()
        self.files={};self.descriptors={}
        for tag,source in [('old6',self.v6),('new7',self.v7)]:
            raw=('copied-only-'+tag).encode();rel='provider_evidence/blobs/'+sha(raw)+'.png'
            old=source/rel;old.parent.mkdir(parents=True,exist_ok=True);old.write_bytes(b'ORIGINAL_MUST_NOT_BE_READ')
            new=self.out/rel;new.parent.mkdir(parents=True,exist_ok=True);new.write_bytes(raw)
            self.files[rel]=sha(raw);self.descriptors[tag]={'path':str(old),'sha256':sha(raw)}
        old={'revision':'triad_repaired_v7','target_out':str(self.v7),'source_out':str(self.v6),
            'copied_files_sha256':{str(Path(self.descriptors['old6']['path']).relative_to(self.v6)):self.descriptors['old6']['sha256']}}
        old['record_hash']=sha(canonical(old).encode())
        self.ancestor='continuation_ancestry/v7_continuation_import.json';ancestor_raw=write(self.out/self.ancestor,old)
        sourceconfig={'continuation':{'source_out':str(self.v6)}}
        sr=write(self.out/'predecessor_provenance/v7/config.json',sourceconfig)
        self.config={'continuation':{'enabled':True,'source_out':str(self.v7),'source_config_sha256':sha(sr),
            'source_plan_sha256':'d'*64,'source_freeze_sha256':'e'*64,'imported_rounds':8,'imported_stages':67,
            'first_new_round':9,'first_new_stage':'REVIEWER'}}
        self.plan={'continuation':{**self.config['continuation'],'original_prefix_chain_head':'a'*64}}
        cr=write(self.out/'config.json',self.config);pr=write(self.out/'plan.json',self.plan)
        write(self.out/'freeze.json',{'evidence_origin':'fixture','config_sha256':sha(canonical(self.config).encode()),'plan_sha256':sha(canonical(self.plan).encode())})
        self.files[self.ancestor]=sha(ancestor_raw);self.files['predecessor_provenance/v7/config.json']=sha(sr)
        self.manifest={'kind':'exact_v7_prefix_continuation_import','revision':'triad_repaired_v8',
            **{k:v for k,v in self.config['continuation'].items()if k!='enabled'},'evidence_origin':'fixture',
            'target_out':str(self.out),'new_allowance':False,'prefix_chain_head':'a'*64,
            'target_config_sha256':sha(cr),'target_plan_sha256':sha(pr),'copied_files_sha256':self.files,
            'direct_reference_files_sha256':{k:v for k,v in self.files.items()if k.endswith('.png')},
            'ancestor_import':{'path':self.ancestor,'sha256':sha(ancestor_raw),'source_out':str(self.v6)}}
        self.seal();self.backend=object.__new__(TriadBackend);self.backend.out=self.out;self.backend.config=self.config
    def tearDown(self):self.temp.cleanup()
    def seal(self):
        self.manifest['record_hash']=sha(canonical({k:v for k,v in self.manifest.items()if k!='record_hash'}).encode())
        raw=write(self.out/'continuation_import.json',self.manifest)
        write(self.out/'live_round_gate.json',{'status':'passed','continuation_import_sha256':sha(raw)})
    def request(self):return {'memory_directory':str(self.out/'cases/same/memory'),
        'image_refs':{'memory:image:0001':deepcopy(self.descriptors['old6']),'memory:image:0002':deepcopy(self.descriptors['new7'])}}
    def test_both_hops_all_roles_use_copied_bytes_without_original_reads(self):
        reader=Path.read_bytes
        def guarded(p):
            if p.is_relative_to(self.v6)or p.is_relative_to(self.v7):raise AssertionError('Original image read')
            return reader(p)
        with patch.object(Path,'read_bytes',guarded):
            for role in 'ABC':
                q=self.request();q['role']=role;before=deepcopy(q);refs=self.backend._references(q)
                for name,d in q['image_refs'].items():self.assertEqual(sha(refs[name]),d['sha256'])
                self.assertEqual(q,before)
    def test_no_fallback_to_missing_or_changed_source(self):
        self.v6.rename(self.root/'original_gone')
        self.assertEqual(sha(self.backend._references(self.request())['memory:image:0001']),self.descriptors['old6']['sha256'])
        copied=self.out/Path(self.descriptors['old6']['path']).relative_to(self.v6);copied.unlink()
        with self.assertRaisesRegex(ContractError,'inside v8'):self.backend._references(self.request())
    def test_outside_unlisted_or_wrong_hash_is_rejected(self):
        for kind in ['outside','unlisted','hash']:
            q=self.request()
            if kind=='outside':q['image_refs']['memory:image:0001']['path']=str(self.root/'outside.png')
            elif kind=='unlisted':q['image_refs']['memory:image:0001']['path']=str(self.v6/'notcopied.png')
            else:q['image_refs']['memory:image:0001']['sha256']='f'*64
            with self.assertRaisesRegex(ContractError,'exact v7/v6'):self.backend._references(q)
    def test_symlink_and_changed_copied_bytes_are_rejected(self):
        p=self.out/Path(self.descriptors['new7']['path']).relative_to(self.v7);old=p.read_bytes();p.write_bytes(b'bad')
        with self.assertRaisesRegex(ContractError,'hash changed'):self.backend._references(self.request())
        p.unlink();p.symlink_to(Path(self.descriptors['new7']['path']))
        with self.assertRaisesRegex(ContractError,'symlink'):self.backend._references(self.request())
    def test_ancestor_cannot_be_redirected_or_self_rehash_hide_gate_change(self):
        self.manifest['ancestor_import']['source_out']=str(self.root/'arbitrary');self.seal()
        with self.assertRaisesRegex(ContractError,'Ancestor alias'):self.backend._references(self.request())
        self.manifest['ancestor_import']['source_out']=str(self.v6);self.seal()
        self.manifest['extra']='changed';self.manifest['record_hash']=sha(canonical({k:v for k,v in self.manifest.items()if k!='record_hash'}).encode())
        write(self.out/'continuation_import.json',self.manifest)
        with self.assertRaisesRegex(ContractError,'integrity gate'):self.backend._references(self.request())
    def test_current_paths_need_no_import_and_old_memory_base_is_rejected(self):
        p=self.out/'current.png';p.write_bytes(b'current')
        self.assertEqual(self.backend._references({'image_refs':{'last_self':{'path':str(p),'sha256':sha(b'current')}}})['last_self'],b'current')
        q=self.request();q['memory_directory']=str(self.v7/'memory')
        with self.assertRaisesRegex(ContractError,'memory directory'):self.backend._references(q)

    def test_actual_retained_R9_descriptors_and_private_drafts_at_both_hops(self):
        repo=Path(__file__).resolve().parents[3]
        source=repo/'runs/main/triad_repaired_v7_live'
        ancestor_file=source/'continuation_import.json'
        if not ancestor_file.is_file():self.skipTest('Actual preserved v7 archive is absent; fixture-only workspace')
        ancestor=json.loads(ancestor_file.read_text());v6=Path(ancestor['source_out'])
        case='case_05_repaired_v6_shared_coached';requests={};descriptors=[]
        for role,stage in zip('ABC',('A_IMAGE_AND_VOTE','B_IMAGE_AND_VOTE','C_VOTE')):
            request=json.loads((source/'cases'/case/'rounds/r09'/(stage+'.request.json')).read_text())
            requests[role]=request;descriptors.extend(request['image_refs'].values())
            outcome=json.loads((source/'cases'/case/'rounds/r09'/(stage+'.outcome.json')).read_text())
            for action in outcome.get('action_log',[]):
                if action.get('generated_image'):
                    d=action['generated_image'];request['image_refs']['memory:image:actualdraft']=d;descriptors.append(d)
        actual=self.root/'actual_v8';actual.mkdir();files={}
        names={p:h for p,h in ancestor['copied_files_sha256'].items()if p.endswith('.png')}
        for d in descriptors:
            path=Path(d['path']);rel=str(path.relative_to(source if path.is_relative_to(source)else v6));names[rel]=d['sha256']
        for rel,h in names.items():
            raw=(source/rel).read_bytes();self.assertEqual(sha(raw),h)
            dest=actual/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw);files[rel]=h
        source_raw=(source/'config.json').read_bytes();p=actual/'predecessor_provenance/v7/config.json';p.parent.mkdir(parents=True);p.write_bytes(source_raw)
        oldraw=ancestor_file.read_bytes();p=actual/self.ancestor;p.parent.mkdir(parents=True);p.write_bytes(oldraw)
        files['predecessor_provenance/v7/config.json']=sha(source_raw);files[self.ancestor]=sha(oldraw)
        config={'continuation':{'enabled':True,'source_out':str(source),'source_config_sha256':sha(source_raw),
            'source_plan_sha256':sha((source/'plan.json').read_bytes()),'source_freeze_sha256':sha((source/'freeze.json').read_bytes()),
            'imported_rounds':8,'imported_stages':67,'first_new_round':9,'first_new_stage':'REVIEWER'}}
        plan={'continuation':{**config['continuation'],'original_prefix_chain_head':'f'*64}}
        cr=write(actual/'config.json',config);pr=write(actual/'plan.json',plan)
        write(actual/'freeze.json',{'evidence_origin':'fixture','config_sha256':sha(canonical(config).encode()),'plan_sha256':sha(canonical(plan).encode())})
        manifest={'kind':'exact_v7_prefix_continuation_import','revision':'triad_repaired_v8',
            **{k:v for k,v in config['continuation'].items()if k!='enabled'},'evidence_origin':'fixture','target_out':str(actual),
            'new_allowance':False,'prefix_chain_head':'f'*64,'target_config_sha256':sha(cr),'target_plan_sha256':sha(pr),
            'copied_files_sha256':files,'direct_reference_files_sha256':names,
            'ancestor_import':{'path':self.ancestor,'sha256':sha(oldraw),'source_out':str(v6)}}
        manifest['record_hash']=sha(canonical(manifest).encode());raw=write(actual/'continuation_import.json',manifest)
        write(actual/'live_round_gate.json',{'status':'passed','continuation_import_sha256':sha(raw)})
        b=object.__new__(TriadBackend);b.out=actual;b.config=config
        reader=Path.read_bytes
        def guarded(path):
            if path.is_relative_to(source)or path.is_relative_to(v6):raise AssertionError('Resolver read original image archive')
            return reader(path)
        with patch.object(Path,'read_bytes',guarded):
            for role,q in requests.items():
                probe={'image_refs':q['image_refs'],'memory_directory':str(actual/'cases'/case/'memory')}
                before=deepcopy(probe);refs=b._references(probe);self.assertEqual(probe,before)
                for name,d in probe['image_refs'].items():self.assertEqual(sha(refs[name]),d['sha256'])


if __name__=='__main__':unittest.main()
