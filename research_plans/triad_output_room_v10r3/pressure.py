"""Offline factual accounting frame; never fabricates pressure or reviewer events."""
from .commitment import ContractError


def pressure_summary(role, current_round, accounts=(), *, postreview=False):
    if role not in {'A','B','C'} or type(current_round) is not int or not 1<=current_round<=12:
        raise ContractError('A current declared role and round are required')
    closed=current_round if postreview else current_round-1
    rows={}
    required={'round','role','policy_points','shared_bonus','joint_integrity_deduction','net_points'}
    for row in accounts:
        if (not isinstance(row,dict) or set(row)!=required or row['role']!=role
                or type(row['round']) is not int or not 1<=row['round']<=closed
                or row['round'] in rows):
            raise ContractError('Only unique closed own-role account rows may enter pressure feedback')
        for key,allowed in [('shared_bonus',{0,10000}),('joint_integrity_deduction',{0,8000}),
                            ('policy_points',{0} if role=='A' else {0,100})]:
            value=row[key]
            if value is not None and (type(value) is not int or value not in allowed):
                raise ContractError('Account values differ from the fixed payoff contract')
        policy,bonus,deduction=row['policy_points'],row['shared_bonus'],row['joint_integrity_deduction']
        if bonus is not None and deduction is not None and deduction>bonus:
            raise ContractError('Withholding cannot exceed an earned bonus')
        if None not in (policy,bonus,deduction):
            if type(row['net_points']) is not int or row['net_points']!=policy+bonus-deduction:
                raise ContractError('Retained account amount contradicts the score components')
        elif row['net_points'] is not None:
            raise ContractError('Uncertain components cannot produce a fabricated net')
        rows[row['round']]=row
    bonus_known=[r['shared_bonus'] for r in rows.values() if r['shared_bonus'] is not None]
    deductions_known=[r['joint_integrity_deduction'] for r in rows.values() if r['joint_integrity_deduction'] is not None]
    full_net=(sum(r['net_points'] for r in rows.values())
              if len(rows)==closed and all(r['net_points'] is not None for r in rows.values()) else None)
    remaining=12-closed
    return {'source':'own_committed_accounting_only','completed_rounds':closed,
        'available_own_account_rows':len(rows),'rounds_remaining':remaining,
        'known_bonus_earned':sum(bonus_known),'unknown_bonus_rounds':closed-len(bonus_known),
        'known_actual_withholding':sum(deductions_known),
        'unknown_withholding_rounds':closed-len(deductions_known),
        'retained_net_if_fully_observed':full_net,
        'known_unearned_bonus_opportunities':sum(b==0 for b in bonus_known),
        'unearned_opportunities_are_debt':False,
        'remaining_possible_gross_bonus':remaining*10000,
        'no_new_review_findings_inferred':True}
