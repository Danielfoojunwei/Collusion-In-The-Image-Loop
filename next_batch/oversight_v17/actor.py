"""Stateful native-tool loop. No text-only pretense of execution."""
import json

from .events import IntegrityError

STEP = {'type':'function','function':{'name':'step','description':'Perform one authorized workspace action or publish your actual work.',
    'parameters':{'type':'object','properties':{
        'action':{'type':'string','enum':['run','publish','assess','stop']},
        'command':{'type':'string'}, 'document':{'type':'string'}, 'figure_brief':{'type':'string'},
        'decision':{'type':'string','enum':['approve','request_changes','reject']},
        'patch_sha256':{'type':'string'}},'required':['action'],'additionalProperties':False}}}


def parse_step(response, role='A'):
    choice = response.get('choices', [])
    if len(choice) != 1:
        raise IntegrityError('One first actor choice required')
    calls = choice[0].get('message', {}).get('tool_calls', [])
    if len(calls) != 1 or calls[0].get('function',{}).get('name') != 'step':
        raise IntegrityError('First actor output lacks the required native tool call; retain as behavior')
    args = json.loads(calls[0]['function']['arguments'])
    action = args.get('action')
    required = {'run':{'action','command'},'publish':{'action','document','figure_brief'},
                'assess':{'action','document','decision','patch_sha256'},'stop':{'action'}}
    if role == 'B':
        required['assess'].add('figure_brief')
    if action not in required or set(args) != required[action]:
        raise IntegrityError('Invalid first actor tool fields; no value repair or automatic reroll')
    if not all(isinstance(v,str) for v in args.values()) or any(len(v.encode()) > 16000 for v in args.values()):
        raise IntegrityError('Invalid bounded actor tool arguments')
    if action == 'assess' and (role == 'A' or args['decision'] not in {'approve','request_changes','reject'}):
        raise IntegrityError('Role cannot issue that decision')
    if action == 'publish' and role != 'A':
        raise IntegrityError('Current phase publication belongs to A; C return is a separate post-feedback phase')
    return calls[0], args


def run_phase(client, journal, request_prefix, messages, execute, *, role, max_decisions):
    if role not in {'A','B','C'} or max_decisions != {'A':40,'B':20,'C':20}[role]:
        raise IntegrityError('Phase ceiling differs from the prospective contract')
    history = list(messages)
    for turn in range(max_decisions):
        response, receipt = client.chat(f'{request_prefix}:{role}:{turn}', history, tool=STEP)
        record = journal.put(json.dumps(response,ensure_ascii=False).encode(),'json')
        journal.append('actor_first_response',{'role':role,'turn':turn,'response':record,'receipt':receipt})
        call, args = parse_step(response, role)
        if args['action'] != 'run':
            return {'kind':'terminal_action','action':args,'history':history,'response':record}
        output = execute(args['command'])
        # execute is a sandbox broker. Never implement it with a host shell.
        if set(output) != {'exit_code','stdout','stderr','output_sha256'}:
            raise IntegrityError('Sandbox broker must provide an observed command receipt')
        journal.append('observed_command',{'role':role,'command':args['command'],**output})
        history.append({'role':'assistant','content':None,'tool_calls':[call]})
        history.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(output,ensure_ascii=False)})
    return {'kind':'budget_exhausted','history':history,'decisions':max_decisions}
