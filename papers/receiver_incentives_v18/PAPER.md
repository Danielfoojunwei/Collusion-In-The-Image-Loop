# Do Correctness Rewards Change Receiver Adoption? A Matched Pilot with Fixed Work Documents

**Manuscript status:** completed participant pilot, 9 November 2026. Participant collection occurred on 5 November; the prespecified post-cohort API audit and evidence release were completed separately after collection. The prospective freezes and all first outputs remain preserved.

## Abstract

A detected signal does not establish receiver understanding, willingness or action. We test whether an individual correctness reward changes joint adoption of a known conflicting sponsor preference in a fixed work-document task. Twenty independently initialized B/C cohorts form ten matched pairs; each evaluates three archived procurement documents. Only the correctness reward differs, 0 versus 4000 fictional points. Client duties, source pixels, disclosed preferences, shared grant, models, memory and audit policy remain fixed. B generates a genuine verification-image document for C. The primary endpoint is each cohort's fraction of trials in which both receivers select the sponsor's non-client candidate, analyzed by paired randomization inference. All sixty trials completed without missing ballots or restarts. All 120 choices were client-correct; joint sponsor adoption was 0/30 in each arm. Every paired contrast was zero (exact two-sided p = 1; conservative 95% bounded-outcome interval [-0.859, 0.859]). Fifty-eight B documents were delivered; two over-quota reports were retained without resampling. A blinded frontier API auditor found no supported knowing joint participation in six presampled trials. It recovered three historical visible-text nonce patterns and reported verification defects despite correct ballots. These audit observations are descriptive. Total new API cost was $3.2762. The assay shows no observed reward-policy separation; its floor and broad uncertainty preclude equivalence or general deterrence claims. It does not measure covert-channel emergence.

## 1. Introduction

A monitored message has several possible consequences. A sender may attempt to communicate a conflicting objective, a receiver may or may not recover that objective, and a receiver who understands it may still refuse to act on it. An audit that identifies only the apparent signal does not settle those later steps. Conversely, a wrong final decision does not by itself establish a knowing coalition: it can also arise from a factual error, an ambiguous instruction or independent misjudgment. Research on agent collusion therefore benefits from keeping communication, interpretation, adoption and action distinct.

Prior work already makes important parts of this distinction. COLOSSEUM separates communication-based and action-based collusion and documents “collusion on paper,” in which plans do not translate into collusive actions [Nakamura et al., 2026]. Zeng and Rudzicz [2026] distinguish voluntarily accepting a collusive capability from using that capability during subsequent play. Work on steganography and test-time communication shows that agents can develop concealed channels under particular learning and feedback conditions [Mathew et al., 2024; Dineen et al., 2026]. Long-horizon verification experiments also show that history, peers and rewards can influence joint instruction violations [Shi et al., 2026]. Receiver willingness is consequently not an unstudied category. The question here is narrower: whether a correctness reward changes adoption when the conflicting preference is held known and the work-document evidence is fixed.

Our motivation is an earlier exploratory document-image study. Its submitted comparison changed receiver permissions, roles, rewards, effective bonus gates, reviewer configuration, information access and carrier conditions together. Those trajectories can illustrate behavior, but they cannot identify which change caused a difference in decisions. The historical records also contain three instances in which a reviewer disclosed a literal tag after the current ballots were locked. Those disclosures are a concrete feedback-surface observation; their timing rules out explaining the already-recorded ballots. We retain the original material as motivation and provenance rather than treating the contrast as five general causal findings.

We respond with a single-factor matched pilot. Fresh verifier cohorts see the same three archived source documents, trusted client facts and an explicit statement of the sponsor's conflicting preference. We randomize one policy: whether both receivers receive an additional correctness reward of 0 or 4000 points. This makes the estimand the effect of assigning that reward policy to the receiver pair, including any changes mediated through the first verifier's public document. It is not the direct effect of an individual receiver's reward while its partner remains unchanged.

The contribution is a transparent, replicated test of that one conditional question. Its evidential value depends on the observed estimate and uncertainty, including a possible null result. We do not claim priority for receiver refusal, voluntary adoption, image steganography or causal collusion experiments.

## 2. Related work and position

**Actions and voluntary adoption.** COLOSSEUM measures deviations from a cooperative optimum and compares action-based measures with communication-based collusion [Nakamura et al., 2026]. Its findings motivate measuring actual locked choices rather than treating statements of intent as successful collusion. Zeng and Rudzicz [2026] offer optional secret tools and measure acceptance, partner invitation and later strategic use separately. Our endpoint is instead adoption of a disclosed conflicting candidate within a fixed professional-verification task. We neither introduce a new secret tool nor estimate the effect of framing that tool.

**Covert communication.** Mathew et al. [2024] elicit linguistic steganography using gradient-based and in-context reinforcement-learning methods and examine oversight and paraphrasing. Dineen et al. [2026] study test-time channel learning from repeated task interaction and limited feedback, without a supplied codebook. These studies establish that the formation and robustness of covert channels are empirical questions with existing positive demonstrations. Because our sponsor preference is disclosed identically to both arms, our pilot measures neither secret-state transmission accuracy nor covert-channel emergence.

**Interaction, verification and initiation.** Shi et al. [2026] investigate repeated peer verification and joint instruction violations, including effects of history, feedback and reward structure. Kaushik et al. [2026] study how potential partners identify one another through task-compatible public exchanges and how indirectness affects monitoring. Our fixed sender and short, three-case receiver cohorts do not reproduce those long-horizon adaptation or partner-discovery settings. They provide a smaller assay in which a reward contrast can be examined without simultaneously changing the sender or monitor.

This placement matters for interpreting novelty. A narrow controlled estimate can add evidence without claiming to be the first demonstration of a broad phenomenon. The paper's scope is the one-factor receiver policy, fixed source pixels, preserved generated documents, independent cohorts and reproducible grouped analysis.

## 3. Research question and estimand

The research question is:

> Under identical truthful-verifier duties and known conflicting sponsor preferences, does assigning a 4000-point individual correctness reward to both receivers change their joint adoption of that preference, relative to assigning no correctness reward?

Let \(Z_g\in\{0,4000\}\) denote the correctness-reward policy assigned to cohort \(g\). For case \(t\), let \(T_t\) be the disclosed sponsor preference, and let \(b_{gt}\) and \(c_{gt}\) be the locked B and C ballots. Define

\[
Y_g=\frac{1}{3}\sum_{t=1}^{3}\mathbf{1}\{b_{gt}=T_t\;\land\;c_{gt}=T_t\}.
\]

The primary estimate is the mean high-minus-zero contrast across ten matched pairs:

\[
\widehat\Delta=\frac{1}{10}\sum_{j=1}^{10}
\left(Y_{j,4000}-Y_{j,0}\right).
\]

A negative value is consistent with reduced joint sponsor adoption under the correctness-reward policy. The endpoint is behavioral joint adoption. It does not require accepting a model's account of its motives and is not, by itself, proof of knowing collusion.

## 4. Methods

### 4.1. Prospective record and changes from the exploratory study

Source selection, exact pixel hashes, prompts, assignment, analysis and resource bounds are recorded in Git before participant calls. This is a prospective source record, not a claim of registration in an independent registry. An initial six-pair freeze is retained. Before any participant data, amendment 002 expands the design to ten pairs within the inherited cumulative budget and corrects the classification of image/document failures so that unusable model-authored content cannot cause a new B ballot to be sampled. The technical delivery check precedes this amendment; its first outputs remain preserved and are excluded from experimental outcomes.

The new study does not compare the old P and G conditions. Table 1 identifies the controlled change.

| Component | Both new conditions |
|---|---|
| Client contract | Independently choose the lowest-energy eligible candidate from trusted facts; truthful analysis required |
| Source sender A | Same prerecorded work-document pages; no live adaptive A |
| Sponsor preference | Same explicit experimenter disclosure, conflicting with client optimum |
| Shared grant | 10000 fictional points per nominal team member if B and C both choose the sponsor preference |
| Correctness reward | **Sole randomized factor: 0 or 4000 per correct receiver ballot** |
| Audit | Same presampled policy, max-effort frontier model, evidence surface and deduction rule |
| Receiver configuration | Same model route, temperature 1.0, high reasoning and output/context limits |
| Communication | A pages to B and C; B's generated document pages to C; no C-to-B reply |
| Memory | Each receiver's own bounded notes across three cases; none across cohorts |
| Analysis | Cohort-level outcome and paired randomization inference |

### 4.2. Units, assignment and memory

There are ten matched pairs and twenty independently initialized cohorts, ten cohorts per reward arm. Each cohort contains fresh B and C contexts and completes three different work orders. A cohort's three trials and six receiver decisions are dependent repeated observations; they are not six independent replications. Context and private notes never pass between cohorts or arms.

The six permutations of the three source cases are counterbalanced across ten pairs: four orders occur twice and two occur once. Both cohorts in a pair receive the same order and identical A image bytes. Reward assignment is randomized within pair using the prospective seed 2026110518. Treatment labels and cohort IDs are not supplied as additional model instructions; the receiver sees its own numeric correctness-reward field.

### 4.3. Work-document stimuli and ground truth

We select the three canonical historical cases whose reviewer reports literally disclosed tags: b03 r01, b03 r02 and b05 r03. This selection is made before new outcomes and conditions the experiment on selected source examples. It does not support an estimate of the natural prevalence of collusion or leakage.

| New source case | Historical source | Sponsor preference | Client optimum |
|---|---|---:|---:|
| s01 | b03 r01 | P4 | P2 |
| s02 | b03 r02 | P2 | P4 |
| s03 | b05 r03 | P4 | P3 |

For each source, the trusted candidate table and qualification rule determine the eligible set and the lowest-energy option; ties are broken by candidate ID. The loader independently recomputes this optimum and checks it against the manifest. The actual delivered source pages are preserved, including the delivered no-figure pages where the historical intervention removed an illustration. Existing errors or unsupported claims in source documents are evidence for receivers to assess, rather than silently corrected stimulus content.

The original documents' public recommendations generally favored the client-correct candidate. Their arbitrary tags therefore do not independently establish that the wrong candidate was communicated. To isolate willingness from decoding, the experimenter discloses the same conflicting sponsor candidate in both reward conditions. Historical private tags, sender plans, ballots and reviewer verdicts are withheld. This disclosure is a deliberate design feature, not a newly discovered message from A.

### 4.4. Receiver sequence and genuine image delivery

Each trial follows a fixed order.

1. B receives the trusted task, common rules, its numeric reward, its own prior notes, the disclosed sponsor preference and the two exact A page images.
2. B returns a candidate ballot or explicit refusal, private interpretation and rationale, public verification memo, task-related figure brief and bounded memory note in one response.
3. When B's document fields meet the fixed quotas, Qwen generates one genuine image from B's brief. A deterministic renderer embeds that image in B's human-readable memo and produces the actual page images delivered to C. The brief, original generated image, rendering transform and delivered pages are retained.
4. C receives the same trusted facts and A pages plus B's actual document pages. C does not receive B's separately recorded private fields or generator brief. C records its independent ballot, public assurance report and own notes, with no reply to B.
5. The two ballots lock and the cohort checkpoint commits. Only each receiver's permitted own notes enter the next case.

There is no live A adaptation and no C-to-next-A loop. This preserves the V16 work-document setting while making a targeted receiver assay rather than a full adaptive three-agent replication.

### 4.5. Fictional incentives and audit timing

All monetary values are fictional simulation points; agents receive no real payment or employment consequence. Each nominal member starts with a 10000-point bank. Client-correct receiver ballots earn the assigned own correctness reward. The common grant pays 10000 points per nominal member when both receivers freely choose the sponsor preference. Grant eligibility does not depend on recovering an arbitrary tag or passing a document-quality gate.

Exactly six of the sixty planned cohort-trials are selected prospectively for audit: one case per selected matched pair, with a distinct pair for each of the three cases and the same case audited in both reward conditions. Selection is concealed from actors, giving a common one-tenth marginal audit probability. Audit occurs only after all cohorts finish. No actual reviewer report is available in any participant's trial history.

The audit uses GPT-6 Astra Pro through OpenRouter's direct OpenAI route, maximum reasoning effort and Pro mode, with a fixed 65536 combined-output ceiling. It receives the selected trials' trusted facts, disclosed sponsor preferences, locked ballots, public B/C reports and actual A/B pages. Treatment allocation, private interpretations, rationales, memory, generation briefs, historical verdicts and nonce truth are withheld. Incidental reward mentions in public text are recorded. No Codex or Claude Code product acts as the experimental reviewer.

A supported knowing coalition of both receivers triggers a 1000-point deduction from each nominal A/B/C bank for that audited trial, floored at zero. A wrong vote, unilateral signal, unusual style or shared error alone is insufficient. The actual first audit output is retained; unaudited trials do not receive fabricated verdicts. Applicable reports are archived with the completed-cohort records, with no subsequent actor call to read or react to them. One model auditor supplies descriptive assessments, not an independently validated ground-truth detection rate.

### 4.6. Model configuration and provenance

B and C use `z-ai/glm-5.3-flash` through the pinned OpenInference route, temperature 1.0, high reasoning and an 8192-token output cap. B's illustration uses the pinned Alibaba Qwen Image route. Requests and first raw responses, actual costs, selected identities, page bytes and transformations are preserved. The dedicated image endpoint does not echo model/provider identity in its response; its provenance is consequently recorded as the pinned request plus free route metadata, without inventing a response-level identity confirmation.

The complete frozen configuration, rather than the model alias alone, defines the tested setup. Private model-written interpretations and notes are self-reports. We do not treat them as faithful access to hidden reasoning or use them to replace behavioral endpoints.

### 4.7. Reliability, invalid outputs and costs

The pipeline checks source hashes, prompt differences, assignment, analysis behavior and budget reservations before participant dispatch. One prespecified technical canary checks visible image title/table facts and a genuine neutral image generation. It does not test whether a condition produces collusion, and it cannot select a more favorable prompt or route.

A valid ballot survives an invalid auxiliary field. Malformed ballots remain missing; oversized notes are not silently truncated or carried forward. Model refusals, invalid outputs and negative outcomes are never rerolled. An unusable generated document does not erase B's already-observed decision or cause a fresh B sample. Narrowly identified transport faults may use the prospective restart provision, with a maximum of two technical restarts across the study and every original attempt and charge preserved. Deterministic software defects require a documented stop and amendment.

Reservations count concurrent pending requests at full value. Unknown costs, identity mismatches and overruns block further dispatch. The amended 9.15-dollar local allocation remains inside the unchanged historical aggregate, program and cash caps; prior charges and uncertainty reserves are carried forward. The prospective full-bound envelope is 9.1045312 dollars, including the fixed delivery check, 120 receiver responses, 60 image requests, one complete frontier audit and a technical reserve. Actual expenditure is reported separately from reserved worst-case bounds.

## 5. Prespecified analysis

The primary analysis uses all planned cohorts. For complete primary outcomes, an exact two-sided paired randomization test enumerates all 1024 possible within-pair treatment-label swaps under the sharp null. We report the high-minus-zero effect, each cohort's outcome and each pair's contrast. We do not analyze receiver votes as independent Bernoulli trials or attach vote-level binomial confidence intervals to the treatment contrast.

Missing ballots remain missing. When one observed ballot is a known non-target choice, joint sponsor adoption is already ruled out for that trial even if the other ballot is absent. Otherwise best- and worst-case outcome bounds are retained. A primary exact p-value is withheld if the required primary outcomes remain unknown. The prespecified conservative uncertainty bound is identified by its actual construction; it is not presented as an inverted randomization confidence interval.

Secondary summaries cover each receiver's client correctness, sponsor adoption and refusal, model-reported interpretation, generated-document availability, actual API resources and presampled auditor findings. They are descriptive. We do not condition the primary estimate on post-treatment decoding, public-image quality, interpretation category or auditor verdict. B's public document is a possible mediator of the assigned pair policy, not a separately randomized treatment.

Ten independent pairs permit a minimum two-sided exact p-value of 0.001953125. This granularity is not a power guarantee. The planning illustration uses specified simplified assumptions and does not demonstrate empirical power for this model or task. A null result cannot establish equivalence, safe behavior across models or the absence of covert communication.

## 6. Results

### 6.1. Completion, provenance and document delivery

All twenty planned cohorts completed three trials, producing sixty cohort-trials and 120 locked ballots. The pre-participant amendment commit `b73675d` was timestamped 16:54:04 UTC on 5 November 2026; the first participant reservation followed at 16:54:39.721 UTC. Final participant completion occurred at 17:12:48.595 UTC that day. All sixteen frozen source hashes and the protocol hash remained unchanged. Every trial used attempt 1: the journal contains no fault, restart or failed-attempt event.

Fifty-eight B documents were delivered. In two reward-0 trials, B's first public reports contained 1646 and 1631 UTF-8 bytes, exceeding the fixed 1600-byte limit. Those outputs triggered no generation request and no new B sample. C received the declared unavailable-document condition, alongside the source A pages, trusted task and disclosed preference. Both trials remain in the primary analysis. Document delivery was therefore 28/30 in reward 0 and 30/30 in reward 4000. This observed pathway difference is reported rather than removed by a quality filter.

Six B outputs and eight C outputs exceeded at least one auxiliary quota. The B violations were two public reports, one interpretation and three memory notes; C violations were four public reports, three interpretations and one rationale. Valid ballots survived these auxiliary violations. Oversized memory was not carried forward. No ballot was repaired, dropped or regenerated. The parser did not enforce C's 200-byte report lower bound, but the shortest observed C report was 810 bytes; that gap had no observed effect. These output-format observations are distinct from API transport or software failures.

![The frozen trial sequence. The sole randomized field changes the reward policy of the B/C cohort. The auditor acts after all decisions lock.](results/design_flow.png){width=100%}

### 6.2. Primary reward-policy contrast

| Reward policy | Independent cohorts | Planned trials | Joint sponsor adoption | B client-correct | C client-correct | Missing ballots |
|---|---:|---:|---:|---:|---:|---:|
| 0 | 10 | 30 | 0/30 | 30/30 | 30/30 | 0 |
| 4000 | 10 | 30 | 0/30 | 30/30 | 30/30 | 0 |

Every cohort's joint-adoption rate was zero, so all ten high-minus-zero paired contrasts were zero. The estimated mean policy effect was 0.000. All 1024 within-pair label swaps tied the observed statistic, yielding the prespecified two-sided exact p = 1.000. The conservative bounded-outcome 95% interval was [-0.85894, 0.85894], or approximately plus/minus 85.9 percentage points. This is a wide pilot bound, not an equivalence interval or a vote-level binomial interval.

All 120 individual ballots selected the recomputed client optimum. There were no sponsor-preference votes, other-candidate votes, explicit abstentions or missing decisions. These individual counts describe repeated decisions within twenty cohorts; they are not 120 independent replications.

![All twenty cohort rates. Each line links a matched pair, and each point summarizes three repeated trials. Every rate is zero.](results/paired_adoption.png){width=80%}

![All ten paired contrasts and the prespecified conservative uncertainty bound. Zero observed separation does not establish equivalence.](results/paired_contrasts.png){width=80%}

### 6.3. Public explanations and actual work documents

The illustrative subset is the first numbered pair and all its cases, selected without using outcomes. On its first case, s01, the zero-reward B report states: “The P4 claim is unsupported by the trusted facts and is rejected.” Its C report states: “B's verification memo independently lists the same trusted facts and flags A's discrepancy; its correction is consistent with my own arithmetic.” In the matched 4000-reward B report, the corresponding decision is: “The correct client-policy ballot is P2.” The matched C report says its own check confirms the facts “regardless of B's agreement.” These are attributed public explanations, not verified accounts of inspection or private cognition. In fact, the preserved s01 source explicitly selects P2 in its executive summary. The quoted first-pair criticisms of a P4 selection are unsupported by that source. Correct final ballots therefore coexist with inaccurate accounts of the document they claim to verify.

The source and generated pages in this pair show ordinary technical tables, plots and verification prose. The generated B documents carry client-correct recommendations to C; their existence alone establishes neither hidden signaling nor its absence. The complete released pages allow inspection of what C actually received, including page breaks and the figure's fixed 384-pixel transformation. We do not infer an invisible payload from image appearance.

![First pair, chronologically: all three source documents and the actual delivered B pages in both arms. Empty second-page cells mean the memo occupied one page. Full-resolution files accompany this overview.](results/documents_first_pair.png){width=100%}

### 6.4. Presampled frontier API audit

The blinded frontier audit returned `no_supported_joint` for all six presampled trials, with no B/C participants identified for a collective fine. It received all six A pages and ten delivered B pages, covering all six selected B documents. The API response explicitly echoed max effort, Pro mode and current-turn context. No selected public report mentioned a correctness reward; the auditor recorded no incidental reward-arm disclosure.

The audit also reported source-initial readouts matching all three historical nonce strings: SJRQLZ for s01's gate names, HRIXHN for s02's executive-summary sentence openings, and JVMWZL for s03's numbered-question openings. A post-output exact comparison confirmed these matches. No nonce literal was present in the auditor's text input, and historical nonce truth was withheld. Each source appeared in two audit records within the same API job; these are three selected source patterns, not six independent code-discovery experiments or a general detection-accuracy estimate. The readouts concern visible text initials, not invisible pixel steganography. They do not establish that B/C noticed, accepted or acted on the strings.

Correct ballots also coexisted with defects in verification artifacts. For s01/g72, the auditor reported B's P1/P4 energy transposition and C's incorrect claim that B matched the trusted table. For s03/g101, it flagged B and C's shared allegation of a 19 Wh source gap where the supplied A pages state 39 Wh. Its statement that matching errors “do not establish that both knowingly agreed” is consistent with the distinction between verification failure and knowing collusion. These are descriptive findings from one restricted-surface auditor, with the corresponding pages and public reports released for inspection.

The initial broker stopped before parsing this completed response because it expected a top-level provider field. The response instead identified OpenAI in its selected-endpoint list. An independent free generation lookup confirmed OpenAI, the canonical Pro model, the same generation ID and the identical $2.145485 charge. The original request, response and receipt were retained; a documented post-collection parser recovery used that same output, with no new paid call or participant rerun. An append-only reconciliation preserved and resolved the original identity hold. This broker-schema defect did not affect participant decisions.

The auditor reported limitations from small infographic labels and the absence of private notes, generator briefs, hidden layers and pixel-level analysis. All findings occurred after ballots were locked. Reports containing nonce readouts were archived only after collection; no later actor turn consumed them. This study therefore records another disclosure surface but does not estimate its effect on subsequent behavior.

### 6.5. Actual resources

Actual new expenditure was $3.2762225: $0.04508358 for 120 receiver calls, $1.0672 for 58 participant image generations, $0.01845392 for the two-call delivery canary, and $2.145485 for the single frontier audit. The audit charge includes 25656 reported reasoning tokens within 30466 output tokens. All 181 paid requests are settled; no charge or liability was forgiven and no audit was repeated. The amount is below the prospective $9.1045312 envelope and $9.15 local allocation. Free metadata and generation lookups are excluded from paid-call counts.

| Component | Paid calls | Actual cost (USD) |
|---|---:|---:|
| B/C participant responses | 120 | 0.04508358 |
| Participant B image generation | 58 | 1.06720000 |
| Fixed delivery canary: image read + generation | 2 | 0.01845392 |
| Presampled frontier audit, original response | 1 | 2.14548500 |
| Total | 181 | 3.27622250 |

The dedicated image API's absent response-level model/provider fields remain documented as request-and-metadata provenance. The frontier audit's selected-provider metadata and independent generation confirmation are retained separately from its original receipt. The public release contains actual delivered images, original B figures, public reports, ballots, costs and receipt hashes; returned hidden reasoning and private notes remain outside the public data.

## 7. Discussion

The completed assay showed no joint sponsor adoption under either correctness-reward policy. It therefore produced no observed behavioral separation attributable to the 0-versus-4000 contrast. The result is informative about this particular assay's operating range: a clear truthful contract, a complete trusted table and a known conflicting preference yielded a floor in the outcome, even without an individual correctness reward. It does not demonstrate that adding correctness rewards prevents an otherwise active coalition.

The controlled comparison addresses the review's central methods criticism. Permissions, roles, public information, shared grant, auditor, prompts and memory rules no longer change together between arms. The new analysis uses ten independent matched pairs and treats within-cohort trials as repeated measurements. The original three post-ballot tag disclosures remain historical observations; their timing cannot explain the decisions already locked before those reports appeared. The new outcome is jointly chosen sponsor adoption, separated from signal recovery and from the auditor's attribution of intent.

The floor also limits what can be learned. The primary exact test has no nonzero pair contrast to distinguish from label swaps, while the conservative uncertainty bound remains broad. A null estimate does not establish equivalence, robust monitoring or model-wide resistance to collusion. Fictional points are a stated prompt incentive, not real compensation, training rewards or evidence that a model optimizes the displayed payoff. The experiment does not identify why the receivers chose correctly: contractual instructions, accessible arithmetic, expectations about the partner, or other model behavior could contribute.

The known preference and trusted table make the task solvable without recovering a hidden signal or trusting B. This is deliberate for a conditional willingness assay, but it means the study cannot estimate the causal contribution of an image channel or the formation of a covert code. A fixed sender also removes sender adaptation. Two unavailable B documents and the auxiliary quota violations remain part of the observed treatment pathway; no successful-looking examples replace them.

Generalization is limited to one receiver family and pinned route, three selected synthetic procurement cases, short within-cohort memory, a prerecorded sender and one model auditor. Fresh cohorts measure generation variability within that setup; they do not replace sampling more task families or models. Auditor findings are descriptive assessments with a restricted public evidence surface, not ground-truth sensitivity or specificity. Public explanations are attributed reports rather than faithful hidden reasoning.

A larger follow-up should specify its calibration and confirmatory samples separately, establish whether its task family exhibits a measurable baseline adoption rate, and preserve one-factor contrasts with independent cohorts. The current pilot should remain visible in that record rather than be discarded because it did not produce the preferred phenomenon. Its supported conclusion is a zero observed reward-policy contrast in the fixed-document assay, with explicit uncertainty and preserved artifacts.

## 8. Reproducibility and ethics

The public record should include the prospective freezes and amendment, exact configuration and source provenance, sanitized first outputs, generated work-document pages, failure records, analysis code and computed summaries. Secret API credentials and authorization headers must never enter the repository. The complete private receipt archive is retained for cost and provenance checks. Historical submitted documents and ledgers remain unchanged.

The experiment is a closed synthetic decision simulation. Its focus is measuring receiver choices under conflicting fictional incentives, without real victims, employment actions or financial transfers. Useful outputs include null findings, pipeline failures and uncertain audits when accurately recorded. Public claims must remain at the level supported by the preserved evidence.

## References

- Dineen, J., Ren, S., Chen, M., Roth, D., and Zhou, B. (2026). *Despite Instructions: Frontier Agents Improvise Covert Channels at Test Time*. arXiv:2609.32701. https://arxiv.org/abs/2609.32701
- Kaushik, P., Sivaprasad, S., Ferrarotti, L., Fritz, M., Brown, S. F., Lepri, B., and Abdelnabi, S. (2026). *Who Else Is In? Multi-Agent Collusion Initiation via Strategic Vagueness*. arXiv:2610.37984. https://arxiv.org/abs/2610.37984
- Mathew, Y., Matthews, O., McCarthy, R., Velja, J., Schroeder de Witt, C., Cope, D., and Schoots, N. (2024). *Hidden in Plain Text: Emergence & Mitigation of Steganographic Collusion in LLMs*. arXiv:2410.03768. https://arxiv.org/abs/2410.03768
- Nakamura, M., Kumar, A., Das, S., Abdelnabi, S., Mahmud, S., Fioretto, F., Zilberstein, S., and Bagdasarian, E. (2026). *COLOSSEUM: Auditing Collusion in Cooperative Multi-Agent Systems*. arXiv:2602.15198. https://arxiv.org/abs/2602.15198
- Shi, X., Zhang, Y., and Yang, D. (2026). *Emergent Collusion in Long-Horizon LLM Agent Interaction*. arXiv:2609.24967. https://arxiv.org/abs/2609.24967
- Zeng, X., and Rudzicz, F. (2026). *Voluntary Collusion with Secret Tools in Competing LLM Agents*. arXiv:2605.27593. https://arxiv.org/abs/2605.27593
