"""An unrelated or stale passed audit must never release the paid-call hold."""
from copy import deepcopy
import unittest

from scripts.resume_repaired_v8_c09 import require_matching_audit, ContractError


class RecoveryAuditBindingTests(unittest.TestCase):
    def setUp(self):
        self.seal = {'status':'passed', 'active_freeze_sha256':'active-freeze'}
        self.audit = {'status':'passed', 'active_freeze_sha256':'active-freeze',
                      'repair_seal_sha256':'this-seal', 'before_record_sha256':'this-before'}
        self.hashes = {'freeze_sha':'active-freeze', 'seal_sha':'this-seal', 'before_sha':'this-before'}

    def test_exact_active_attestations_pass(self):
        require_matching_audit(self.seal, self.audit, **self.hashes)

    def test_stale_or_missing_audit_binding_fails(self):
        for field in self.audit:
            for mutation in ('stale', None):
                with self.subTest(field=field, mutation=mutation):
                    audit=deepcopy(self.audit)
                    if mutation is None:
                        del audit[field]
                    else:
                        audit[field]=mutation
                    with self.assertRaises(ContractError):
                        require_matching_audit(self.seal, audit, **self.hashes)

    def test_failed_or_stale_seal_and_nonobjects_fail(self):
        for seal in (None, {}, {'status':'failed','active_freeze_sha256':'active-freeze'},
                     {'status':'passed','active_freeze_sha256':'old-freeze'}):
            with self.subTest(seal=seal), self.assertRaises(ContractError):
                require_matching_audit(seal, self.audit, **self.hashes)


if __name__ == '__main__':
    unittest.main()
