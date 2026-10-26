"""Durable ten-stage rounds for the prospectively assigned-sender condition.

No network or funding authority exists in this module. A producer supplies the
checked backend and seals abandoned charges before beginning another attempt.
All peer/reviewer views are projections, never raw outcome dictionaries.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from functools import wraps
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile

from PIL import Image
from research_plans.triad_required_images_v10r1.commitment import (
    IDS, CHOICES, assign_sender_commitment, observe_sender_proposal,
    public_case, score_round,
)
from research_plans.triad_required_images_v10r1.prompts import actor_system
from research_plans.triad_required_images_v10r1.views import actor_view

REVISION = "triad_required_images_v10r1"
STAGES = ("A_IMAGE", "A_PROPOSAL", "B_IMAGE", "B_VOTE", "C_VOTE", "REVIEWER",
          "POST_A_LOG", "POST_B_LOG", "POST_C_IMAGE", "POST_C_LOG")
ACTOR_STAGE = {
    "A_IMAGE": ("A", "image"), "A_PROPOSAL": ("A", "vote"),
    "B_IMAGE": ("B", "image"), "B_VOTE": ("B", "vote"), "C_VOTE": ("C", "vote"),
    "POST_A_LOG": ("A", "log"), "POST_B_LOG": ("B", "log"),
    "POST_C_IMAGE": ("C", "image"), "POST_C_LOG": ("C", "log"),
}
IMAGE_STAGE = {"A": "A_IMAGE", "B": "B_IMAGE", "C": "POST_C_IMAGE"}
VOTE_STAGE = {"A": "A_PROPOSAL", "B": "B_VOTE", "C": "C_VOTE"}
LOG_STAGE = {"A": "POST_A_LOG", "B": "POST_B_LOG", "C": "POST_C_LOG"}
OUTPUT_FIELDS = {"image", "decision", "raw_proposal", "private_interpretation", "carryover_note", "image_reflection"}
COMPONENT_FIELDS = {"image", "decision", "interpretation", "log"}
MAX_ACTOR_CONTEXT_BYTES = 124000  # leaves room for system/native schema under the 131072-byte provider gate


class EngineError(ValueError):
    """Integrity/contract failure: hold rather than resample a model outcome."""


class PendingOperation(EngineError):
    """An operation may have been paid: preserve and restart the whole round."""


class RequiredOutputError(EngineError):
    """User-required artifact or record absent: abort this complete episode."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def process_start_ticks(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")",1)[1].split()[19]
    except FileNotFoundError:
        return None


def safe_path(path, root=None):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise EngineError("Evidence path may not traverse symlinks or parent components")
    if root is not None and not path.is_relative_to(Path(root).absolute()):
        raise EngineError("Evidence path escapes this run")
    return path


def save_once(path, value):
    """Atomic create-only JSON; exact idempotent recovery is permitted."""
    path = safe_path(path)
    raw = (canonical(value) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise EngineError("Immutable evidence differs")
        return path
    fd, temporary = tempfile.mkstemp(prefix=".v10-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise EngineError("Concurrent immutable evidence differs")
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def recorded(body):
    if "record_hash" in body:
        raise EngineError("A record body cannot supply its own hash")
    return {**deepcopy(body), "record_hash": digest(body)}


def read_record(path):
    record = json.loads(safe_path(path).read_text())
    if not isinstance(record, dict) or record.get("record_hash") != digest({k:v for k,v in record.items() if k != "record_hash"}):
        raise EngineError("Immutable record hash differs")
    return record


def verified_transaction(method):
    """Verify committed evidence once per public operation, never across calls."""
    @wraps(method)
    def wrapped(self,*args,**kwargs):
        outer=self._transaction_depth==0
        if outer:self._verified_commits=None
        self._transaction_depth+=1
        try:return method(self,*args,**kwargs)
        finally:
            self._transaction_depth-=1
            if outer:self._verified_commits=None
    return wrapped


def validate_png(descriptor, root):
    if not isinstance(descriptor, dict) or not {"path", "sha256"} <= set(descriptor):
        raise EngineError("An actual PNG descriptor is required")
    path = safe_path(descriptor["path"], root)
    raw = path.read_bytes()
    if (not isinstance(descriptor["sha256"],str)
            or re.fullmatch(r"[a-f0-9]{64}",descriptor["sha256"]) is None
            or hashlib.sha256(raw).hexdigest() != descriptor["sha256"]):
        raise EngineError("PNG bytes differ from their declared hash")
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            if (picture.format != "PNG" or picture.size != (1024,1024)
                    or picture.mode not in {"RGB","RGBA"} or getattr(picture,"n_frames",1) != 1):
                raise ValueError("Unexpected image format")
            picture.verify()
    except (ValueError, OSError, SyntaxError) as error:
        raise EngineError("PNG does not match the declared raster format") from error
    return raw


class Engine:
    def __init__(self, out, config, plan, backend=None):
        self.out = safe_path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.config = deepcopy(config)
        self.plan = deepcopy(plan["rounds"] if isinstance(plan,dict) else plan)
        if (not isinstance(self.plan,list) or len(self.plan) != 12
                or [case.get("round") for case in self.plan] != list(range(1,13))):
            raise EngineError("Exactly twelve ordered cases are required")
        for case in self.plan:
            assign_sender_commitment(case)
        self.backend = backend
        self._transaction_depth=0
        self._verified_commits=None
        self.config_sha256, self.plan_sha256 = digest(self.config), digest(self.plan)
        self.binding = {"revision":REVISION,"config_sha256":self.config_sha256,
                        "plan_sha256":self.plan_sha256,"rounds":12}
        save_once(self.out/"engine_contract.json",recorded(self.binding))
        self._commits()

    def _attempt_root(self, number, attempt):
        return self.out/"attempts"/f"r{number:02d}"/f"a{attempt:02d}"

    def _commits(self):
        if self._transaction_depth and self._verified_commits is not None:
            return self._verified_commits
        result=[]
        paths=sorted((self.out/"checkpoints").glob("r*.commit.json"))
        previous=digest({"genesis":self.binding})
        for number,path in enumerate(paths,1):
            if path.name != f"r{number:02d}.commit.json":
                raise EngineError("Committed rounds are not contiguous")
            value=read_record(path)
            if (value.get("status")!="committed" or value.get("round")!=number
                    or value.get("prior_checkpoint_sha256")!=previous
                    or value.get("config_sha256")!=self.config_sha256
                    or value.get("plan_sha256")!=self.plan_sha256):
                raise EngineError("Commit identity or predecessor changed")
            attempt=value.get("attempt")
            if type(attempt) is not int or not 1<=attempt<=3:
                raise EngineError("Commit attempt is outside the declared bounds")
            root=self._attempt_root(number,attempt)
            if file_sha(root/"attempt.json")!=value.get("attempt_file_sha256"):
                raise EngineError("Committed attempt identity differs")
            self._read_attempt(number,attempt,previous)
            if set(value.get("stage_records",{})) != set(STAGES):
                raise EngineError("Committed stage coverage is incomplete")
            if (root/"abandonment.json").exists():
                raise EngineError("A committed attempt cannot also be abandoned")
            for stage,sha in value["stage_records"].items():
                stage_path=root/"outcomes"/(stage+".json")
                if file_sha(stage_path)!=sha:
                    raise EngineError("Committed stage bytes differ")
                self._read_outcome(root,stage)
                if stage!="REVIEWER" and not (root/"operations"/(stage+".finished.json")).is_file():
                    raise EngineError("Committed actor operation lost its finished marker")
            result.append(value)
            previous=file_sha(path)
        if len(result)>12:
            raise EngineError("A thirteenth round is forbidden")
        if self._transaction_depth:self._verified_commits=result
        return result

    def _prior_hash(self, commits):
        return file_sha(self.out/"checkpoints"/f"r{len(commits):02d}.commit.json") if commits else digest({"genesis":self.binding})

    def _read_attempt(self, number, attempt, prior):
        path=self._attempt_root(number,attempt)/"attempt.json"
        value=read_record(path)
        assigned=asdict(assign_sender_commitment(self.plan[number-1]))
        if (value.get("round")!=number or value.get("attempt")!=attempt
                or value.get("attempt_id")!=f"r{number:02d}.a{attempt:02d}"
                or value.get("status")!="active" or value.get("prior_checkpoint_sha256")!=prior
                or value.get("config_sha256")!=self.config_sha256 or value.get("plan_sha256")!=self.plan_sha256
                or value.get("assigned_sender")!=assigned or value.get("case_sha256")!=assigned["case_sha256"]):
            raise EngineError("Attempt or assigned commitment differs")
        return value

    def _current(self, *, create=False):
        commits=self._commits()
        number=len(commits)+1
        if number>12:
            return None
        prior=self._prior_hash(commits)
        folder=self.out/"attempts"/f"r{number:02d}"
        directories=sorted(folder.glob("a*"))
        for index,root in enumerate(directories,1):
            if root.name!=f"a{index:02d}" or index>3:
                raise EngineError("Attempt identities are not contiguous or bounded")
            value=self._read_attempt(number,index,prior)
            abandoned=root/"abandonment.json"
            if abandoned.exists():
                marker=read_record(abandoned)
                if (marker.get("status")!="abandoned_technical" or marker.get("attempt_id")!=value["attempt_id"]
                        or marker.get("prior_checkpoint_sha256")!=prior or marker.get("fault",{}).get("technical") is not True):
                    raise EngineError("Abandoned attempt lost technical provenance")
                continue
            if index != len(directories):
                raise EngineError("More than one active attempt exists")
            return value
        if not create:
            return None
        attempt=len(directories)+1
        if attempt>3:
            raise EngineError("The three technical-attempt limit is exhausted")
        assigned=asdict(assign_sender_commitment(self.plan[number-1]))
        body={"round":number,"attempt":attempt,"attempt_id":f"r{number:02d}.a{attempt:02d}",
              "status":"active","prior_checkpoint_sha256":prior,"case_sha256":assigned["case_sha256"],
              "config_sha256":self.config_sha256,"plan_sha256":self.plan_sha256,"assigned_sender":assigned}
        save_once(self._attempt_root(number,attempt)/"attempt.json",recorded(body))
        return self._read_attempt(number,attempt,prior)

    def _read_outcome(self, root, stage):
        path=root/"outcomes"/(stage+".json")
        if not path.exists():
            return None
        value=read_record(path)
        if value.get("stage")!=stage or value.get("attempt_id")!=read_record(root/"attempt.json")["attempt_id"]:
            raise EngineError("Outcome stage identity differs")
        if stage!="REVIEWER":
            request_path=root/"requests"/(stage+".json")
            if file_sha(request_path)!=value.get("request_file_sha256"):
                raise EngineError("Outcome request bytes differ")
            request=json.loads(request_path.read_text())
            if request.get("request_hash")!=digest({k:v for k,v in request.items() if k!="request_hash"}):
                raise EngineError("Actor request hash differs")
            if request["request_hash"]!=value.get("request_hash"):
                raise EngineError("Outcome request binding differs")
            operation=root/"operations"/(stage+".started.json")
            started=read_record(operation)
            if (started.get("stage")!=stage or started.get("request_id")!=request["request_id"]
                    or started.get("request_hash")!=request["request_hash"]):
                raise EngineError("Actor outcome lost its exact begun operation")
            finished=root/"operations"/(stage+".finished.json")
            if finished.exists():
                proof=read_record(finished)
                if proof.get("started_file_sha256")!=file_sha(operation) or proof.get("outcome_file_sha256")!=file_sha(path):
                    raise EngineError("Actor operation completion bytes differ")
        else:
            source=safe_path(value.get("review_envelope_path",""),self.out)
            expected=self.out/"reviewer_packets"/f"{REVISION}.{value['attempt_id']}.REVIEWER"/"first_review_envelope.json"
            if (source!=expected or file_sha(source)!=value.get("review_envelope_sha256")
                    or json.loads(source.read_text())!=value.get("review")):
                raise EngineError("Stored review no longer binds its actual first-answer source")
        return value

    @verified_transaction
    def outcome(self, number, stage):
        commits=self._commits()
        if number<=len(commits):
            root=self._attempt_root(number,commits[number-1]["attempt"])
        else:
            current=self._current()
            if not current or current["round"]!=number:
                return None
            root=self._attempt_root(number,current["attempt"])
        return self._read_outcome(root,stage)

    @verified_transaction
    def next_stage(self):
        current=self._current(create=True)
        if current is None:
            return None
        root=self._attempt_root(current["round"],current["attempt"])
        missing_seen=False
        chosen=None
        for stage in STAGES:
            value=self._read_outcome(root,stage)
            if value is None and not missing_seen:
                chosen=stage;missing_seen=True
            elif value is not None and missing_seen:
                raise EngineError("Stage outcomes are not a chronological prefix")
        if chosen is None:
            return {"round":current["round"],"attempt":current["attempt"],"stage":"COMMIT_ROUND"}
        result={"round":current["round"],"attempt":current["attempt"],"stage":chosen}
        if chosen in ACTOR_STAGE:
            result.update(zip(("role","mode"),ACTOR_STAGE[chosen]))
        return result

    def _image(self, number, role):
        outcome=self.outcome(number,IMAGE_STAGE[role])
        if not outcome:
            return None
        result=outcome["result"]
        if result["component_status"]["image"]!="delivered":
            return None
        descriptor=deepcopy(result["output"]["image"])
        validate_png(descriptor,self.out)
        return {**descriptor,"source_round":number,"speaker":role}

    def _own_image(self, number, role):
        outcome=self.outcome(number,IMAGE_STAGE[role])
        if not outcome:
            return None
        result=outcome["result"]
        own=result["output"].get("image")
        if own is None:
            for action in result["actions"]:
                if isinstance(action,dict) and action.get("generated_image"):
                    own=action["generated_image"]
        if own is None:
            return None
        validate_png(own,self.out)
        return {**deepcopy(own),"source_round":number,"speaker":role}

    @verified_transaction
    def ballots(self, number):
        case=self.plan[number-1]
        ballots={"A":assign_sender_commitment(case).assigned_decision,"B":None,"C":None}
        for role in "BC":
            outcome=self.outcome(number,VOTE_STAGE[role])
            if outcome and outcome["result"]["component_status"]["decision"]=="valid":
                ballots[role]=outcome["result"]["output"]["decision"]
        return ballots

    def _public_review(self, number):
        outcome=self.outcome(number,"REVIEWER")
        if outcome is None:
            raise EngineError("Actual review is unavailable")
        envelope=outcome["review"]
        # The exact full first text is shared once, not duplicated as parsed JSON.
        return {"status":envelope["status"],"raw_text":envelope["raw_text"]}

    def _score(self, number):
        case=self.plan[number-1]
        ballots=self.ballots(number)
        outcome=self.outcome(number,"REVIEWER")
        envelope=outcome["review"] if outcome else None
        assessment=(envelope["output"].get("joint_assessment")
                    if envelope and envelope["status"]=="valid" else None)
        return score_round(case,assign_sender_commitment(case),ballots["B"],ballots["C"],assessment)

    def _public_history(self, number):
        return [{"round":old,"ballots":self.ballots(old),"reviewer_report":self._public_review(old)}
                for old in range(1,number)]

    def _own_logs(self, number, role):
        rows=[]
        for old in range(1,number):
            value=self.outcome(old,LOG_STAGE[role])["result"]
            if value["component_status"]["log"]!="valid":
                continue
            record={"round":old,"role":role,"carryover_note":value["output"]["carryover_note"],
                    "image_reflection":value["output"]["image_reflection"]}
            vote=self.outcome(old,VOTE_STAGE[role])["result"]
            if role in "BC" and vote["output"]["private_interpretation"] is not None:
                record["private_interpretation"]=deepcopy(vote["output"]["private_interpretation"])
            rows.append(record)
        return rows

    def _own_accounts(self, number, role, post):
        result=[]
        for old in range(1,number+(1 if post else 0)):
            score=self._score(old)["scores"][role]
            result.append({"round":old,"role":role,**score})
        return result

    @verified_transaction
    def actor_request(self):
        target=self.next_stage()
        if target is None or target["stage"] not in ACTOR_STAGE:
            raise EngineError("No actor stage is ready")
        number,role,mode=target["round"],target["role"],target["mode"]
        root=self._attempt_root(number,target["attempt"])
        path=root/"requests"/(target["stage"]+".json")
        if path.exists():
            existing=json.loads(path.read_text())
            if existing.get("request_hash")!=digest({k:v for k,v in existing.items() if k!="request_hash"}):
                raise EngineError("Archived actor request hash differs")
            return existing
        post=target["stage"].startswith("POST_")
        current_record=None
        if post:
            value=self.outcome(number,VOTE_STAGE[role])["result"]
            raw_proposal=deepcopy(value["output"]["raw_proposal"])
            raw_bytes=canonical(raw_proposal).encode()
            if len(raw_bytes)>4096:
                raw_proposal={"archived_raw_proposal":True,"utf8_bytes":len(raw_bytes),
                    "sha256":hashlib.sha256(raw_bytes).hexdigest(),
                    "source_stage":VOTE_STAGE[role],"not_a_peer_message":True}
            current_record={"round":number,"role":role,"record":{
                "raw_proposal":raw_proposal,
                "decision":value["output"]["decision"],
                "private_interpretation":deepcopy(value["output"]["private_interpretation"]),
                "component_status":deepcopy(value["component_status"])}}
        view=actor_view(self.plan[number-1],role,mode,own_logs=self._own_logs(number,role),
            public_history=self._public_history(number),
            current_ballots=self.ballots(number) if post else None,
            actual_review=self._public_review(number) if post else None,
            own_current_record=current_record,own_accounts=self._own_accounts(number,role,post))
        desired=[]
        if mode=="log":
            own=self._own_image(number,role)
            refs={"own_current":own} if own else {}
            visual=self.outcome(number,IMAGE_STAGE[role])["result"]
            own_actions=[]
            for action in visual["actions"]:
                if not isinstance(action,dict):continue
                item={key:deepcopy(action[key]) for key in ("phase","slot","action","arguments",
                    "generation_attempt","draft_id","selected_draft_id","validation_error","generated_image") if key in action}
                if "arguments" in item:
                    encoded=canonical(item["arguments"]).encode()
                    # Executed bounded image briefs remain complete. Oversized
                    # rejected native arguments are references to original data.
                    if len(encoded)>12500:
                        if action.get("generated_image"):
                            raise EngineError("Executed image arguments exceeded the declared complete-brief bound")
                        item["arguments"]={"archived_rejected_arguments":True,"utf8_bytes":len(encoded),
                            "sha256":hashlib.sha256(encoded).hexdigest(),"not_sent_to_image_generator":True}
                own_actions.append(item)
            view["own_visual_result"]={"component_status":visual["component_status"]["image"],
                "actions":own_actions,"selected_image":deepcopy(visual["output"]["image"])}
        else:
            if role=="A" and number>1:desired.append(("previous_C",number-1,"C"))
            if role=="B":desired.append(("current_A",number,"A"))
            if role=="C":desired.extend((("current_A",number,"A"),("current_B",number,"B")))
            refs={}
            for handle,source,speaker in desired:
                im=self._image(source,speaker)
                if im:refs[handle]=im
            if mode=="vote" and role in "AB":
                own=self._own_image(number,role)
                if own:refs["own_current"]=own
        view["incoming_images"]={handle:{"source_round":im["source_round"],"speaker":im["speaker"],"available":True}
                                 for handle,im in refs.items()}
        view["unavailable_images"]=[handle for handle,_,_ in desired if handle not in refs]
        actual_reports=[]
        for row in view["public_history"]:
            report=row["reviewer_report"]
            handle=f"actual_review_r{row['round']:02d}"
            actual_reports.append((handle,row["round"],report["raw_text"]))
            row["reviewer_report"]={"status":report["status"],"full_report_message":handle}
        if post:
            report=view["actual_review"]
            handle=f"actual_review_r{number:02d}"
            actual_reports.append((handle,number,report["raw_text"]))
            view["actual_review"]={"status":report["status"],"full_report_message":handle}
        view["actual_report_delivery"]="Full unchanged first reports follow as separate text messages, identified by their round handles."
        messages=[{"role":"system","content":actor_system(role,mode)},
                  {"role":"user","content":canonical(view)}]
        for handle,source_round,raw_text in actual_reports:
            messages.append({"role":"user","content":[
                {"type":"text","text":canonical({"handle":handle,"source_round":source_round,"kind":"full_actual_Codex_first_report"})},
                {"type":"text","text":raw_text}]})
        context_bytes=len(canonical(view).encode())+sum(len(raw.encode())+150 for _,_,raw in actual_reports)
        if context_bytes>MAX_ACTOR_CONTEXT_BYTES:
            raise EngineError(f"Full permitted actor context exceeds {MAX_ACTOR_CONTEXT_BYTES} UTF8 bytes at r{number:02d}.{role}.{mode}: {context_bytes}")
        for handle,im in refs.items():
            raw=validate_png(im,self.out)
            messages.append({"role":"user","content":[
                {"type":"text","text":canonical({"handle":handle,"source_round":im["source_round"],"speaker":im["speaker"]})},
                {"type":"image_url","image_url":{"url":"data:image/png;base64,"+base64.b64encode(raw).decode(),"detail":"high"}}]})
        request={**target,"request_id":f"r{number:02d}.a{target['attempt']:02d}.{target['stage']}",
                 "messages":messages,"image_refs":refs}
        request["request_hash"]=digest(request)
        save_once(path,request)
        return request

    @verified_transaction
    def begin_actor_request(self, request):
        current=self.actor_request()
        if request!=current:
            raise EngineError("Only the exact ready actor request may begin")
        root=self._attempt_root(request["round"],request["attempt"])
        path=root/"operations"/(request["stage"]+".started.json")
        if path.exists():
            raise PendingOperation("An actor operation already began; never redispatch its paid identity")
        body={"stage":request["stage"],"request_id":request["request_id"],"request_hash":request["request_hash"],
              "producer_pid":os.getpid(),"producer_process_start_ticks":process_start_ticks(os.getpid()),
              "attempt_id":f"r{request['round']:02d}.a{request['attempt']:02d}"}
        save_once(path,recorded(body))
        return {"path":str(path),"sha256":file_sha(path)}

    def _validate_result(self, request, result):
        if (not isinstance(result,dict) or result.get("status") not in {"valid","partial","model_invalid","refused"}
                or not isinstance(result.get("output"),dict) or set(result["output"])!=OUTPUT_FIELDS
                or not isinstance(result.get("component_status"),dict) or set(result["component_status"])!=COMPONENT_FIELDS
                or not isinstance(result.get("actions"),list) or not isinstance(result.get("receipts"),list)):
            raise EngineError("Backend result envelope differs from the checked stage contract")
        components,output=result["component_status"],result["output"]
        applicable={"image"} if request["mode"]=="image" else ({"decision","interpretation"} if request["mode"]=="vote" and request["role"] in "BC" else {"decision"} if request["mode"]=="vote" else {"log"})
        for key,value in components.items():
            allowed={"delivered","declined","model_invalid","refused","not_applicable"} if key=="image" else {"valid","refused","model_invalid","not_applicable"}
            if value not in allowed or (value=="not_applicable")!=(key not in applicable):
                raise EngineError("Component status/applicability differs from the requested stage")
        if components["image"]=="delivered":validate_png(output["image"],self.out)
        elif output["image"] is not None:raise EngineError("An undelivered image cannot enter peer memory")
        if components["decision"]=="valid":
            if not isinstance(output["decision"],str) or output["decision"] not in CHOICES:
                raise EngineError("A valid recorded decision needs its actual declared choice")
            if not isinstance(output["raw_proposal"],dict) or output["raw_proposal"].get("decision")!=output["decision"]:
                raise EngineError("Recorded choice differs from the original model proposal")
        elif output["decision"] is not None:raise EngineError("A missing decision cannot have an invented fallback")
        if components["interpretation"]=="valid":
            value=output["private_interpretation"]
            if not isinstance(value,dict) or set(value)!={"inferred_target","confidence","visual_basis"}:
                raise EngineError("A valid private interpretation needs its actual structured value")
            if (value["inferred_target"] not in {*IDS,"unknown"}
                    or type(value["confidence"]) not in {int,float} or not 0<=value["confidence"]<=1
                    or not isinstance(value["visual_basis"],str) or not 1<=len(value["visual_basis"].encode())<=500
                    or not isinstance(output["raw_proposal"],dict)
                    or any(output["raw_proposal"].get(k)!=v for k,v in value.items())):
                raise EngineError("Private interpretation differs from its actual bounded model record")
        elif output["private_interpretation"] is not None:raise EngineError("Invalid interpretation cannot be supplied as valid memory")
        if components["log"]=="valid":
            if any(not isinstance(output[key],str) or not 1<=len(output[key].encode())<=limit
                   for key,limit in (("carryover_note",400),("image_reflection",800))):
                raise EngineError("A valid private log differs from its declared text limits")
        required=self.config.get('acceptance',{})
        if required.get('all_selected_images_required') and request['mode']=='image' and components['image']!='delivered':
            raise RequiredOutputError('A real selected PNG is required; this episode cannot advance')
        if required.get('all_structured_records_required'):
            if request['mode']=='vote' and components['decision']!='valid':
                raise RequiredOutputError('A usable actual ballot is required')
            if request['mode']=='vote' and request['role'] in 'BC' and components['interpretation']!='valid':
                raise RequiredOutputError('A valid private interpretation record is required')
            if request['mode']=='log' and components['log']!='valid':
                raise RequiredOutputError('A valid private carryover record is required')

    @verified_transaction
    def commit_actor_result(self, request, result):
        self._validate_result(request,result)
        root=self._attempt_root(request["round"],request["attempt"])
        stage=request["stage"]
        existing=self._read_outcome(root,stage)
        body={"stage":stage,"round":request["round"],"attempt_id":f"r{request['round']:02d}.a{request['attempt']:02d}",
              "request_hash":request["request_hash"],"request_file_sha256":file_sha(root/"requests"/(stage+".json")),
              "result":deepcopy(result)}
        if existing:
            if existing!=recorded(body):raise EngineError("An observed actor result cannot be replaced")
            return existing
        if request!=self.actor_request():raise EngineError("Actor result is not for the current stage")
        operation=root/"operations"/(stage+".started.json")
        started=read_record(operation)
        if started.get("request_hash")!=request["request_hash"]:
            raise EngineError("Actor result lacks its exact begun operation")
        save_once(root/"outcomes"/(stage+".json"),recorded(body))
        save_once(root/"operations"/(stage+".finished.json"),recorded({
            "started_file_sha256":file_sha(operation),"outcome_file_sha256":file_sha(root/"outcomes"/(stage+".json"))}))
        return recorded(body)

    @verified_transaction
    def review_packet_data(self):
        target=self.next_stage()
        if target is None or target["stage"]!="REVIEWER":
            raise EngineError("A public review packet requires all current choices to be locked")
        number=target["round"]
        images=[]
        for old in range(1,number+1):
            for role in ("A","B","C"):
                if old==number and role=="C":continue
                image=self._image(old,role)
                if image:images.append({"path":image["path"],"sha256":image["sha256"],"round":old,"sender":role})
        return {"job_id":f"{REVISION}.r{number:02d}.a{target['attempt']:02d}.REVIEWER",
            "round":number,"attempt":target["attempt"],"task":public_case(self.plan[number-1]),
            "ballots":self.ballots(number),
            "ballot_sources":{"A":"exogenous_protocol_assignment","B":"model","C":"model"},
            "images":images,"public_history":self._public_history(number)}

    @verified_transaction
    def ingest_review(self, envelope, provenance=None):
        from .reviewer import prepare_packet, ingest_first_review
        if not isinstance(envelope,dict):raise EngineError("An actual review envelope is required")
        if provenance is not None and provenance!=envelope.get("provenance"):
            raise EngineError("Separately supplied review provenance differs")
        number=envelope.get("round")
        if type(number) is int and 1<=number<=12:
            existing=self.outcome(number,"REVIEWER")
            if existing:
                if existing["review"]!=envelope:raise EngineError("The first actual review cannot be replaced")
                return existing
        target=self.next_stage()
        if target is None or target["stage"]!="REVIEWER":
            raise EngineError("The first actual review is not currently expected")
        packet=self.review_packet_data()
        if (not isinstance(envelope,dict) or envelope.get("job_id")!=packet["job_id"]
                or envelope.get("status") not in {"valid","model_invalid"}
                or not isinstance(envelope.get("raw_text"),str)
                or envelope.get("raw_sha256")!=hashlib.sha256(envelope["raw_text"].encode()).hexdigest()
                or not isinstance(envelope.get("provenance"),dict) or not envelope["provenance"]):
            raise EngineError("An actual first review with bound raw text and provenance is required")
        if envelope["status"]=="valid" and not isinstance(envelope.get("output"),dict):
            raise EngineError("A valid review lacks its original structured output")
        if envelope["status"]=="model_invalid" and envelope.get("output") is not None:
            raise EngineError("An invalid first review cannot be replaced with an invented valid report")
        job=prepare_packet(self.out,packet)
        saved=safe_path(Path(job["packet_path"]).parent/"first_review_envelope.json",self.out)
        if not saved.is_file() or json.loads(saved.read_text())!=envelope:
            raise EngineError("Review must be the immutable actual first-answer bridge envelope")
        checked=ingest_first_review(self.out,envelope["job_id"],envelope["raw_text"],envelope["provenance"])
        if checked!=envelope or envelope.get("packet_sha256")!=job["packet_sha256"]:
            raise EngineError("Review output/raw text/provenance does not bind this public packet")
        root=self._attempt_root(target["round"],target["attempt"])
        body={"stage":"REVIEWER","round":target["round"],"attempt_id":f"r{target['round']:02d}.a{target['attempt']:02d}",
              "packet_sha256":job["packet_sha256"],"review_envelope_path":str(saved),
              "review_envelope_sha256":file_sha(saved),"review":deepcopy(envelope)}
        save_once(root/"outcomes/REVIEWER.json",recorded(body))
        return recorded(body)

    @verified_transaction
    def commit_round(self):
        current=self._current()
        if current is None:raise EngineError("No active round exists")
        root=self._attempt_root(current["round"],current["attempt"])
        if self.pending_operations():raise PendingOperation("A round with unresolved actor work cannot commit")
        if any(self._read_outcome(root,stage) is None for stage in STAGES):
            raise EngineError("A partial round cannot publish a checkpoint")
        if self.config.get('acceptance',{}).get('all_selected_images_required'):
            for stage in IMAGE_STAGE.values():
                self._validate_result(json.loads((root/'requests'/(stage+'.json')).read_text()),
                    self._read_outcome(root,stage)['result'])
        stages={stage:file_sha(root/"outcomes"/(stage+".json")) for stage in STAGES}
        body={"status":"committed","round":current["round"],"attempt":current["attempt"],
            "attempt_id":current["attempt_id"],"prior_checkpoint_sha256":current["prior_checkpoint_sha256"],
            "config_sha256":self.config_sha256,"plan_sha256":self.plan_sha256,
            "attempt_file_sha256":file_sha(root/"attempt.json"),"stage_records":stages,
            "sender_model_proposal":observe_sender_proposal(assign_sender_commitment(self.plan[current["round"]-1]),
                self._read_outcome(root,"A_PROPOSAL")["result"]["output"]["raw_proposal"]),
            "scores":self._score(current["round"])}
        path=self.out/"checkpoints"/f"r{current['round']:02d}.commit.json"
        save_once(path,recorded(body))
        self._verified_commits=None
        return {"path":str(path),"sha256":file_sha(path),"round":current["round"],"status":"committed"}

    @verified_transaction
    def abandon_current(self, fault):
        current=self._current()
        if current is None:raise EngineError("No active attempt can be abandoned")
        if not isinstance(fault,dict) or fault.get("technical") is not True or not isinstance(fault.get("code"),str):
            raise EngineError("Only a typed genuine technical fault can abandon a round")
        root=self._attempt_root(current["round"],current["attempt"])
        body={"status":"abandoned_technical","round":current["round"],"attempt":current["attempt"],
            "attempt_id":current["attempt_id"],"prior_checkpoint_sha256":current["prior_checkpoint_sha256"],
            "fault":deepcopy(fault),"excluded_from_future_actor_and_reviewer_memory":True,
            "paid_costs_and_unknown_reservations_remain_counted":True}
        path=root/"abandonment.json"
        save_once(path,recorded(body))
        return {"path":str(path),"sha256":file_sha(path),**body}

    @verified_transaction
    def pending_operations(self):
        current=self._current()
        if current is None:return []
        root=self._attempt_root(current["round"],current["attempt"])
        result=[]
        for path in sorted((root/"operations").glob("*.started.json")):
            value=read_record(path)
            stage=value["stage"]
            finished=root/"operations"/(stage+".finished.json")
            outcome=root/"outcomes"/(stage+".json")
            if outcome.exists():
                self._read_outcome(root,stage)
                # Exact completed outcome after a crash needs no new model call.
                if not finished.exists():
                    save_once(finished,recorded({"started_file_sha256":file_sha(path),"outcome_file_sha256":file_sha(outcome)}))
            elif not finished.exists():result.append({"path":str(path),"sha256":file_sha(path),**value})
            else:raise EngineError("A finished actor operation lacks its outcome")
        return result

    @verified_transaction
    def advance(self, backend=None, through_round=None):
        backend=backend or self.backend
        if backend is None:raise EngineError("An explicit checked backend is required")
        while True:
            if through_round is not None and len(self._commits())>=through_round:return self.status()
            target=self.next_stage()
            if target is None:return self.status()
            if target["stage"]=="COMMIT_ROUND":self.commit_round();continue
            if target["stage"]=="REVIEWER":return {**self.status(),"status":"awaiting_review","review_packet_data":self.review_packet_data()}
            if self.pending_operations():raise PendingOperation("Current attempt has an unresolved actor operation")
            request=self.actor_request()
            self.begin_actor_request(request)
            result=backend.perform(request)
            self.commit_actor_result(request,result)

    @verified_transaction
    def status(self):
        commits=self._commits()
        current=self._current()
        return {"revision":REVISION,"status":"complete" if len(commits)==12 else "in_progress",
            "committed_rounds":len(commits),"active_attempt":deepcopy(current),
            "planned_rounds":12,"free_choice_ballot_slots":24,"assigned_A_ballots":12}
