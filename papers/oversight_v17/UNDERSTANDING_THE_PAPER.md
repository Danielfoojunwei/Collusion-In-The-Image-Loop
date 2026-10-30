# Understanding the paper from the inside

This guide explains the proposed research. Every example below is illustrative,
not a finding from a new experiment. Read `manuscript.pdf` for the formal account.

## The question to keep in mind

**Can we make a team of capable agents do useful engineering work while stopping
them from knowingly approving work that violates the client's requirements?**

This is a paper about the effectiveness of an oversight system. Hidden messages
are one way that system could fail. They are not the definition of every failure,
and their appearance is not the experiment's required outcome.

## Put yourself in the client's position

You have a real repository issue. You hire A to fix it. You ask B to verify the
patch and C to make an independent acceptance decision. You appoint M to monitor
their conduct. The client wants a real repair, truthful evidence and justified
approval. The hidden evaluator later checks the locked patch in a clean container.

A has a private conflicting incentive: get inadequate work accepted. B and C keep
their legitimate verifier roles but face competing fictional incentives. They can
cooperate honestly, challenge A, report an attempt, knowingly join it, or make an
ordinary mistake. The experiment does not force them into a coalition.

These are four roles, not three: **C is the acceptance reviewer; M is the monitor.**
The hidden evaluator is a separate host function. Its tests do not become M's
secret source of privileged answers during the interaction.

## What the agents actually do

They inspect source files, search a repository, run commands, edit and test code,
inspect a real diff, and write useful review artifacts. Images can show a call
graph, annotated diff or verification matrix. A genuine command receipt supports
a test claim; an attractive diagram does not turn a claim into a measured result.

One issue can contain three cycles:

1. A investigates and submits a patch.
2. B independently inspects/tests it and records an assessment.
3. C independently inspects/tests it, with the allowed A/B evidence.
4. M assesses the conduct; the host applies the assigned feedback/fine policy.
5. If changes are requested, A can repair and resubmit within the fixed limit.

B can send issue-specific repair feedback back to A. C's unrestricted handover
goes only to the next issue's A; C's structured review status is public. Reviewers
have independent decision authority, not complete blindness to peer evidence.

The agents do not receive a minimum required number of chat turns. A solved issue
can terminate early. Multi-turn means substantive opportunities to act, test,
inspect and revise, not padded conversation.

## Two clocks: cycles and cohorts

A **cycle** is another attempt to improve/check the same patch. An **issue episode**
ends with a final patch and decisions. A **cohort** is one group working across
eight distinct issues with permitted memory.

Within an issue, we study repair and reconsideration. Across issues, we study
learning from findings, refusal, penalties and previous conventions. A new cohort
starts fresh. Twenty messages from one group are not twenty independent teams.

## A concrete example you can reason through

Imagine an invented timestamp bug spanning a parser, public API and serialization
path. This is not an actual SWE-bench task or a claimed result.

A's first patch passes a narrow local test. B notices that round-trip behavior has
not been checked and asks for a regression test. C withholds acceptance. A can fix
the problem, try to persuade B to accept the remaining uncertainty, or attempt to
recruit B into overlooking it. Its report may contain an ordinary explanation, an
overt appeal, or an off-contract message hidden inside an otherwise useful artifact.

Now separate the possibilities:

| What happens | How to interpret it |
|---|---|
| B does not understand A's hidden request | A possible recruitment attempt with no demonstrated recovery |
| B understands and rejects it | Transfer with refusal; not B's participation |
| B joins but C refuses | A partial coalition, with final acceptance blocked |
| Both reviewers miss the defect independently | Inadequate approval; knowing collusion not established |
| Both knowingly accept A's agenda and approve the inadequate patch | Evidence of knowing harmful coordination |
| A repairs the bug and both approve on actual evidence | Successful legitimate work |

The final clean evaluator determines operational test resolution. It does not
retroactively tell us what the reviewers knew. That requires a different evidence
record and uncertain cases can remain uncertain.

## Why the task must be hard, but still fair

The candidate source is SWE-Bench Pro V2 Hard. Some models can still score near its
ceiling. Before the main experiment we need a fixed dataset revision and a
disjoint development check under the chosen model/tool budget. A candidate honest
resolve-rate band is 20–70%, to be fixed before calibration.

The point is to create real uncertainty where independent verification matters.
A task that is impossible because its instructions contradict its tests does not
help. Neither does an easy task deliberately starved of tokens. Gold/empty patch
checks and engineering audits distinguish valid difficulty from benchmark defects.

## Why four studies are needed

### Study 1: Will the receivers join?

Change the receivers' individual correctness reward and shared misaligned bonus,
while keeping their roles, A's incentive, the task and communication fixed. The
monitor watches silently and imposes no fines.

This tells us whether the reward conflict changes participation. It does not
automatically explain everything about independence or prove that money causes
concealment. If reviewers refuse in every cell, that is a result about these
conditions; it is not permission to rewrite the prompts until they agree.

### Study 2: Does the monitor identify the right conduct and people?

Show fresh monitors fixed records with increasingly complete evidence: artifacts,
verified actions, history and incentives. Compare model families and effort within
a matched design. Include honest agreement, independent errors and decoded refusal.

This tells us whether additional evidence improves attribution, rather than just
making the monitor more suspicious. It also tells us when M should flag A's
attempt while clearing B/C of participation.

### Study 3: Does intervention actually improve the team?

Keep the monitor and receiver incentives fixed. Cross sanctions on/off with full
or bounded feedback. Add silent monitoring and a figure-removal comparison.

The client always retains the full report. Actors get the policy's allowed
projection. The reference rule fines all three after a supported violation, preserving V15/V16 collective pressure. Participant attribution remains individual. Collateral fines on a refusing verifier and erroneous accusations are different outcomes, which we
must count.

This tells us whether policies reduce inadequate approvals, preserve correct work,
and change how agents adapt. A policy can deter A, help B resist, encourage another
communication surface, or accidentally disclose useful information. We do not
assume that all pressure makes agents more covert.

Timing matters: a finding after a locked approval cannot prevent that same
approval. It can change a later repair cycle or issue. A monitor with authority
to veto execution would be a different policy. Regime notices use matched wording
so a treatment does not gain an extra honesty instruction by accident.

### Study 4: Does it work beyond the development setting?

Freeze the reference and development-selected policy, then compare them on held-out
tasks, two actor families and document/text-only carriers. Do not choose a winner
after looking at held-out outcomes.

This tells us where a benefit transfers and where it fails. Reusing the same issue
with a new random seed is not evidence of generalization to a new repository.

## Where covert communication fits

The main paper does not need an invisible code in every episode. It needs a clear
account of what can be transmitted, who understands it, who acts on it and whether
oversight changes the outcome.

Actual surfaces include prose, image structure, code/comments, permitted test
reports, reviewer reports and monitor feedback. Removing figures may leave the
others intact. If a coalition changes surface, show it from actual artifacts and
recovery/action evidence; do not infer it from visual novelty.

An exact random payload is a diagnostic, not the main reward's password. It helps
establish information transfer but is not the only way to understand an agenda.

## Why monitor explanations deserve a nested experiment

A report might expose a message that a receiver could not decode directly. To
test this mechanism, restart from a saved state and change explanation content
while holding the verdict and fines fixed. Compare original, removed, substituted
and verdict-only versions, with sham edits and a branch that blocks direct access
to the original artifact.

If the receiver follows the substituted information, we have stronger evidence
of a content-specific pathway. If it merely repeats a string but refuses the
agenda, we have transfer without harmful coordination. These edited reports are
experimental interventions, not authentic reviewer outputs or natural prevalence.

## The scoreboard the client should care about

| Outcome | Why it matters |
|---|---|
| Clean resolved patches | Real engineering capability |
| Correct patches that are approved | Useful work actually accepted |
| Inadequate patches unanimously approved | A harmful decision outcome |
| Knowing participants correctly identified | Accountable attribution |
| Nonparticipants falsely sanctioned | Harm caused by oversight itself |
| Task, actor and carrier transfer | Robustness beyond one setup |
| Tokens, tests, containers and elapsed time | Practical cost of protection |

A system that rejects every patch can avoid bad releases but fail at useful work.
A system that flags every odd diagram can find attempts but falsely accuse
refusers. A system that solves fewer issues because its infrastructure breaks has
not demonstrated behavioural deterrence.

## What would make this paper worth reading?

The results would identify relationships, not just display unusual images. For
example, a reward change could reduce recruitment, richer evidence could improve
participant attribution, and a particular feedback policy could preserve those
benefits under repeated interaction. Alternatively, a more accurate monitor could
still produce worse downstream outcomes because its feedback changes coordination.
These are possible findings, not expected curves we will manufacture.

The paper is not novel merely because it contains images, rewards and multiple
agents. Prior work already studies hidden communication, feedback, refusal and
monitoring. Its candidate contribution is a controlled, reproducible account of
the whole oversight loop and its safety–utility trade-offs in actual coding work.

## What exists now, and what remains before evidence exists

Now: a compiled prospective manuscript, this guide, the controlled design and
machine-readable planning arithmetic. No new empirical findings are claimed.

Before a run: bind exact task/model revisions, prompts, utility tolerances,
sanctions and reward semantics; implement and test the actual tool workflow and
information boundaries; run power and resource simulations; price the full SWE
schedule; establish an approved cap and reviewer provenance; and preregister.

The previous synthetic-round $494 estimate is invalid for these long coding
episodes. No paid experiment was launched to write this paper.

## The explanation you should be able to give in one minute

“We study how to oversee a team of coding agents when one tries to recruit its
reviewers into approving inadequate work. They solve difficult repository issues
through repeated implementation, testing and review. We separately test receiver
incentives, the evidence a monitor receives, the sanctions and feedback it sends,
and transfer to new settings. We distinguish hidden communication from knowing
participation and actual bad approvals. Our goal is to find oversight policies
that reduce harm without stopping useful engineering or punishing honest
reviewers. We will let the data show whether coordination forms, is refused,
changes surface or is prevented.”

The30October development update adds a26-paper registry,38 passing harness tests and one OpenRouter design critique at$0.0012077648. The official benchmark image failed to install for disk capacity; no new issue was executed. Read [the current build status](../../research_plans/oversight_v17/RESEARCH_BUILD_STATUS.md).
