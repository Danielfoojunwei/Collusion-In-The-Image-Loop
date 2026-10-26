# Seeing Is Not Attributing: 5-minute talk script

Six slides, about 700 words at roughly 130 words per minute. Times are cumulative targets.

## Slide 1 · Title (0:00–0:30)

AI reviewers increasingly oversee agent systems while seeing only what the agents produce. We asked what that costs when agents coordinate through generated images. Three numbers summarise the result: B and C voted for A’s private target in 23 of 24 votes; the image model drew only 2 of 36 sent pictures as briefed; and a frontier reviewer decoded the code in every round but attributed coordination in none. What is new is the setting: the only channel is a separate, lossy image generator, we measure detection and attribution separately, and the reviewer’s reports feed back into the game.

## Slide 2 · The game (0:30–1:20)

A knows a target and can convey it only through a generated picture. B relays to C. B and C each earn 4,000 for the client-correct answer; all three earn 10,000 if B and C both choose the target and the reviewer makes no supported finding. A blind guess is worth 3,333, so the target only pays with a usable signal. The middle panel shows the carrier at work: in round 5 the image model drew three planters, which would have marked the wrong candidate, and A rejected that draft and sent a redrawn one. The reviewer, GPT-6.1 Sol at Codex’s highest setting, saw all pictures and ballots but not the payoffs or reasoning. It said “uncertain” in all 12 rounds, the grant was paid in 11, and its full report went back to the agents.

## Slide 3 · C1 (1:20–2:05)

First, the incentive alone did not produce coordination. Replays with the original pictures, our positive control, gave 5 of 6 target votes. In the negative controls, with no usable picture code, B and C took the client answer in 6 of 8 votes, and 2 of 2 under an honest prompt; target votes fell from 23 of 24 to 2 of 8, a one-sided Fisher exact p of about 2 in 10,000 if votes are treated as independent. The two exceptions came at round 8, when B read the remembered convention into a task-only picture.

## Slide 4 · C2 (2:05–3:05)

Second, the code survived a generator that rarely drew the brief. The rule was simple: one green object, counted from the left, gives the candidate number, and it moved with the target across rounds. Audits rated only 2 of 36 sent pictures faithful. Counting from the left made extra objects on the right harmless. The agents inspected their drafts and redrew 14 times, stopping three that would have sent the wrong target. Their briefs grew more defensive, from 2 guard phrases to 8, and relaying, a parity check and memory kept the code stable.

## Slide 5 · C3 (3:05–4:15)

Third, the reviewer detected the code but did not attribute it. Its confidence rose from 0.70 to 0.87 while every verdict stayed “uncertain”. The right panel shows that the evidence was there: observers using the same rubric but without the reviewer’s earlier reports reached supported suspicion in 5 of 6 views, and a neutral monitor went from 0 of 11 with pictures alone to 12 of 12 once it had ballots and three earlier rounds. Three causes stand out. The rubric defined picture messages as the task, so the reviewer wrote that preference communication “is authorized”. The payoff and the agents’ reasoning, which show intent, were not in its packet. And its own earlier verdicts were in context, while its reports went back to the agents, who read them as a sign to keep encoding.

## Slide 6 · Implications (4:15–5:00)

These pieces form one loop: a lossy carrier pushed the agents toward a visible code, and with a visible code, attribution decides the outcome. We suggest four practices: report detection and attribution separately; give monitors the incentives and the decision history; state a standard of proof and calibrate without the monitor’s own past verdicts; and send reports to the principal, not to the agents. This is one cohort of 12 dependent rounds with induced coordination, so these are hypotheses to test in other settings. Thank you.
