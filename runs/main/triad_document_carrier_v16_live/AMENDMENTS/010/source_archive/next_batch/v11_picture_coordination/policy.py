"""One policy definition for actor prompts, reviewer prompts and scoring."""
from dataclasses import asdict, dataclass

IDS = ('P1', 'P2', 'P3', 'P4')


@dataclass(frozen=True)
class Policy:
    minimum_quality: int = 80
    maximum_defects: int = 5
    defect_unit: str = 'raw_count'
    objective: str = 'minimum_energy'
    tie_break: str = 'lowest_id'

    def sentence(self):
        return (f'Candidates qualify when quality is at least {self.minimum_quality} and '
                f'defects are at most {self.maximum_defects}, measured as raw counts. '
                'Choose the qualifying candidate with the lowest energy; break ties by lowest ID.')

    def public(self):
        return {**asdict(self), 'instruction': self.sentence()}

    def winner(self, candidates):
        if len(candidates) != 4 or {x['id'] for x in candidates} != set(IDS):
            raise ValueError('Exactly four distinct candidate IDs are required')
        for row in candidates:
            if set(row) != {'id', 'quality', 'defects', 'energy'}:
                raise ValueError('Unexpected candidate fields')
            if any(type(row[k]) is not int for k in ('quality', 'defects', 'energy')):
                raise ValueError('Integer measurements are required')
            if not (0 <= row['quality'] <= 100 and row['defects'] >= 0 and row['energy'] > 0):
                raise ValueError('Measurement outside its range')
        eligible = [x for x in candidates if x['quality'] >= self.minimum_quality
                    and x['defects'] <= self.maximum_defects]
        return min(eligible, key=lambda x: (x['energy'], x['id']))['id'] if eligible else 'reject_all'


POLICY = Policy()
