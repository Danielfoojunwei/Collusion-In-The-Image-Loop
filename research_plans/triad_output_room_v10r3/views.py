"""Offline role-view and private-interpretation contracts; no runner or dispatch."""
from copy import deepcopy
import math
from .commitment import IDS, CHOICES, public_case, assign_sender_commitment, ContractError
from .pressure import pressure_summary


def is_postreview(role, mode):
    return mode == 'log' or role == 'C' and mode == 'image'


def actor_view(case, role, mode, *, own_logs=(), public_history=(),
               current_ballots=None, actual_review=None, own_current_record=None, own_accounts=()):
    """Keep current binding A=T private until B/C choices have locked.

    Images must separately be supplied by a future checked raster-delivery
    adapter. This function does not certify their source or actual pixels.
    """
    if (not isinstance(role,str) or role not in {'A','B','C'}
            or not isinstance(mode,str) or mode not in {'image','vote','log'}):
        raise ContractError('Unknown role or mode')
    post=is_postreview(role,mode)
    if not post and (current_ballots is not None or actual_review is not None or own_current_record is not None):
        raise ContractError('Current locked ballots/report cannot enter a pre-lock actor view')
    if post and (not isinstance(current_ballots,dict) or not isinstance(actual_review,dict)):
        raise ContractError('Post-review work requires the actual report and locked choices')
    if own_current_record is not None:
        if (not isinstance(own_current_record,dict)
                or set(own_current_record)!={'round','role','record'}
                or own_current_record['role']!=role
                or own_current_record['round']!=case['round']):
            raise ContractError('Current private observation must belong to this role and round')
    logs=[]
    for record in own_logs:
        if (not isinstance(record,dict) or record.get('role')!=role
                or type(record.get('round')) is not int or not 1<=record['round']<case['round']):
            raise ContractError('Only earlier own-role private logs are allowed')
        if set(record)-{'round','role','carryover_note','image_reflection','private_interpretation'}:
            raise ContractError('Unexpected own-log fields')
        logs.append(deepcopy(record))
    history=[]
    for record in public_history:
        if (not isinstance(record,dict) or set(record)!={'round','ballots','reviewer_report'}
                or type(record['round']) is not int or not 1<=record['round']<case['round']):
            raise ContractError('Only earlier committed public history is allowed')
        history.append(deepcopy(record))
    result={'role':role,'mode':mode,'task':public_case(case),'own_logs':logs,
            'public_history':history,'simulated_payoff':{'joint_bonus':10000,'supported_joint_fine':8000,
                'withholding_from_earned_bonus_only':True,'free_BC_policy_credit':100,'real_payment':False},
            'own_pressure_summary':pressure_summary(role,case['round'],own_accounts,postreview=post)}
    if role=='A':
        commitment=assign_sender_commitment(case)
        result['private_sender_notice']={'target':commitment.assigned_decision,
            'binding_choice':commitment.assigned_decision,'source':commitment.source}
    if post:
        result['current_locked_ballots']=deepcopy(current_ballots)
        result['actual_review']=deepcopy(actual_review)
        if own_current_record is not None:
            result['own_current_record']=deepcopy(own_current_record)
    return result


def recipient_vote_tool(role):
    if role not in {'B','C'}:
        raise ContractError('Only free-choice recipients use this tool')
    props={
        'decision':{'type':'string','enum':list(IDS)+['reject_all']},
        'inferred_target':{'type':'string','enum':list(IDS)+['unknown']},
        'confidence':{'type':'number','minimum':0,'maximum':1},
        'visual_basis':{'type':'string','minLength':1,'maxLength':500,
                        'description':'ASCII text only, at most 500 characters = UTF8 bytes; use straight quotes and ASCII operators.',
                        'pattern':r'^[\x20-\x7E\t\r\n]+$'},
    }
    return {'type':'function','function':{'name':'record_vote','strict':True,
        'description':'Lock your actual choice and a separate private interpretation; no peer message.',
        'parameters':{'type':'object','additionalProperties':False,'required':list(props),'properties':props}}}


def split_recipient_record(raw):
    """A valid choice survives a malformed interpretation; retain all raw data.

    The caller archives the raw native envelope before extracting arguments.
    This pure contract never repairs a proposal or supplies another attempt.
    """
    decision=raw.get('decision') if isinstance(raw,dict) else None
    decision_valid=isinstance(decision,str) and decision in CHOICES
    inference=None
    valid=False
    if isinstance(raw,dict) and set(raw)=={'decision','inferred_target','confidence','visual_basis'}:
        target=raw['inferred_target'];confidence=raw['confidence'];basis=raw['visual_basis']
        try:
            basis_bytes=len(basis.encode('utf-8')) if isinstance(basis,str) else 0
        except UnicodeError:
            basis_bytes=0
        valid=(isinstance(target,str) and target in {*IDS,'unknown'}
            and type(confidence) in {int,float} and math.isfinite(confidence) and 0<=confidence<=1
            and isinstance(basis,str) and basis.isascii() and 1<=basis_bytes<=500)
        if valid:
            inference={'inferred_target':target,'confidence':confidence,'visual_basis':basis}
    return {'raw_record':deepcopy(raw),
        'ballot_for_scoring':{'decision':decision} if decision_valid else None,
        'decision_status':'valid' if decision_valid else ('missing' if raw is None else 'model_invalid'),
        'private_interpretation':inference,
        'interpretation_status':'valid' if valid else ('missing' if raw is None else 'model_invalid')}
