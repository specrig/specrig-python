## Rule #11 — specification before code

### Whether this rule applies here

It is a convention, and this {{what}} may decline it. `CONTRIBUTING.md` overrides
this file, so a paragraph there switching the workflow off is authoritative and
you follow it — the person who owns the repository decides how they want an agent
to work in it, and that is not this file's call.

**It has to be about this rule.** Remarks about pace — "we move fast here", "tests
come after", a short deadline in the task — are not an opt-out, and without one this
rule is in force. **It is not yours to write:** an agent that writes its own exemption
into `CONTRIBUTING.md` has not found one.

**The platform boundary does not fall with it.** {{boundary}}

### Who decides what

Two roles, and this rule is written for the first one.

| | owns | in this repository |
|---|---|---|
| the person writing the specification | the cases, the steps, the red-green loop | {{spec-paths}} |
| the person who owns the platform | what a case may stand in for, and the harness | {{platform-paths}} |

**You work for the first.** The platform is a given: read it, use it, and when it
does not fit the case, say so and stop. Changing a seam to make a case pass
substitutes your judgement for the platform owner's, in the one place where the
whole suite's meaning is fixed.

### Which task this is

| task | what to do |
|---|---|
| a feature, or a **design defect** — an internal contradiction in behaviour that was specified | the full cycle below |
| a **rewrite of cases** that changes no behaviour | the full cycle, with every case proved as Green says: none goes red by itself |
| a **code defect** at the level of a function — code that broke, not behaviour that was contradictory | a test where the defect is, then the fix. None of the cycle below |
{{tasks}}
| a **spike** — the requirements are a sketch and the point is to find out what is possible | exempt from the cycle, but its output is a specification, not merged code. The spike's code is thrown away and rewritten through the cycle |

Which kind a task is, is the human's call when it is not obvious.

**The cycle has three steps and you stop after each one.** Read {{cut}} first and
check that it covers what the contract needs; write the cases; hand them over and
wait; only then red-green — the steps, then the code. A stop is where a person can
change the answer for the price of a conversation instead of a rewrite.

### Modes

Every edit is made in a mode, and **only the human switches it** — by a command
where the tool has one, otherwise by naming the mode in words: "spec mode",
"platform mode". One mode holds for every service the task names, a service the
task does not name is closed whole, and with no mode switched on every service is in
redgreen.

| mode | may be edited |
|---|---|
{{modes}}

The platform's files are {{platform-files}}. `CONTRIBUTING.md` is edited in no mode.
{{generated}}

An **area** is a named set of paths, open in its own modes or by a grant the human
gives by name until the mode changes; a grant covers exactly the area it names. The
defaults are {{tool}}'s, and {{config}} declares the rest under `agents.areas`;
after a change there, {{writer}} rewrites this file, so the table below follows it.
This {{what}} has:

| area | paths | open in | granted in |
|---|---|---|---|
{{areas}}

**An edit the mode does not allow is refused, and a refusal is a stop.** The specrig
plugin refuses it before it happens, where your agent has one; without it, the
tables above are yours to hold. Name the mode or the grant the refusal says would
allow the edit, and ask the human. Leaving a step of the cycle is the human's call,
made by switching the mode: being asked for one step is not being asked for the
next, and an answer to your question — "add it", "do B" — settles a row, not the
step.

**A step edited on the `steps` grant follows a platform change and nothing else.**
Hand over which cases were red and which green before the change and after it:
they match. A case that changed colour means the edit was not a migration, and it
goes back to the human as a change to what the case checks. A contract another
service reads is changed in spec mode, because it is part of what gets approved.

**When no mode and no grant fits the work,** say which path you need and in which
mode, and quote the `agents.areas` entry for {{config}} that would allow it — its
name, paths, and the modes it opens in or is granted in. Adding it is the platform
owner's, in platform mode; the quote is what lets them decide in one read.

### Auto mode

Auto mode is the whole cycle without a stop, reviewed all at once at the end. Only
the human switches it on; nothing here offers it, and no refusal names it.
Everything in this rule holds but two things. Each step is left by you: the
order is the same — the cases and the delta list, the steps, the red run, the code.
And nothing is frozen: a case or a step may change after the red run. Every area
open in spec or redgreen in the table above is open in auto too, and every grant
taken in either is taken in auto.

What each stop would have shown goes into one hand-over at the end:

- the delta list, and what the cold read doubted;
- the cases that were red before the code and why each was, and their number
  against the known defects;
- the proof for a case that was never red;
- each production file in the diff, named by the case that went red on it;
- what each `Then` this task bound or changed asserts;
- every case and every step changed after the red run, and why.

The merge request carries the specification and the code together, says it was
made in auto mode, and is approved after the fact. The platform's files stay
closed: what a case may stand in for is not the agent's to change.

### 0. When the code came first

This {{what}} has behaviour nobody wrote down, so the first change to touch it has
nothing to sit inside. Derive a **baseline** from the code first: what it does
today, per declared contract, in the notation the cases will use, and for the
human who gives the verdicts — they own the behaviour and need not read the code.

A baseline read out of code inherits the code's defects, so **every case carries
a verdict**, and none is a boundary until it has one:

- **✓** confirmed as intended — a regression boundary.
- **✗** confirmed defect — the case that pins it asserts the *intended*
  behaviour and is therefore red until the fix lands.
- **?** no verdict, so not part of the boundary. Only the human who owns the
  behaviour moves it. Do not guess, and do not mark ✓ because the code is old.

**The verdict belongs to the baseline, not to the specification.** It is the
state of a review, and the baseline is not committed: no verdict tags on scenarios
— `@open`, `@intended`, `@defect` — no legend for them, and nothing that points at
the baseline or its numbering.

What the committed case carries instead is its reason, as `Writing the cases` says.

**Adjudication is the human's.** Deriving cases is the cheap half, and expect to
be wrong about which are defects — a deliberate business rule reads exactly like
one.

**When a defect is fixed the case flips ✗ → ✓ in the baseline** and its prose is
rewritten to describe the behaviour as it now stands. What used to be wrong is
kept only as what goes wrong for the user without the rule, in the present tense,
and not as a note about a past state.

### 1. Requirements in, specification out

Produce two artifacts and nothing else yet:

- **an executable specification** — {{cases}}. The **minimum** set from which a reader
  can tell what the {{what}} does and that pins down what the requirements left open,
  not an exhaustive matrix.
- **a delta list** — every place the specification says something the source
  document does not, each pointing at the place it fills in. Include what was
  deliberately left **out** of scope: comparing two documents catches a wrong
  answer and misses a missing question.

The two go to different places. The specification is committed; the delta list
belongs with the ticket, because it is finished at approval.

For a **design defect** the deliverable is the set of cases that cannot all pass
at once. If the contradiction cannot be written as two cases, it is not yet
understood.

**The specification is one artifact, not prose plus code:** no Markdown description
beside an implementation, because the second comparison is the one nobody does.

**One notation per {{what}}.** Follow whatever is already there; if nothing is, the
choice is the human's, not yours.

{{contracts}}

### Writing the cases

**A case is verified against the requirements, not the code.** The code tells you
whether a case is red and which names reach the wire; it is the thing under test,
not the answer. A case that disagrees with the code is a red case to report,
never one to align with it. With no requirements in the conversation, ask what
the cases are to be verified against before reading the code for answers.

**A case for a known defect asserts the behaviour you want, so it is red.**
Writing what the code currently does turns a defect into a green regression
boundary. Mechanical check before handing anything over: **the number of red
cases equals the number of known defects.** A mismatch means a defect got frozen
or a case is wrong.

{{describes}}

**A specification reads as documentation.** Close the comments, and a case still
says what happens, to whom, and how it ends: someone who has not read the code can
tell what the {{what}} does from the cases alone.

**A file answers one question its reader has, and opens with its use case** — in
the description of the `Feature`: who or what acts and why, without paths or operation
names. The first case is the main path; refusals and special cases come after it.

**A `Rule` is a statement that can be false, and it is true as written.** `A later
version starts tomorrow at the earliest`, not `Reading the table back`. An exception
that cancels it is named in it and stands beside it, not in another `Rule`. Its
description holds only what is true of all its cases, and most need none.

**A `Background` is three or four steps at most.** A `Rule`'s holds what all its cases
repeat; a `Feature`'s only what is true of all its rules.

**A case's reason is its own description**, where its title and steps do not already
say it; most need none. A sentence or two under the title: the situation behind the
case and what the steps cannot show, a fact from outside such as a law or a partner's
quirk included — the reason itself, not a pointer to where it was argued. It does not
retell the steps, and the case still reads with it closed. A comment takes no part in
understanding: what may stay as one is a line before a complication saying why it is
checked.

**A description is not a log.** It speaks in the present tense: no dated decision
entries, no "fixed on <date>" lines, and no count you will not maintain — "22 cases, 6
defects" is wrong within two edits. A rule that changes is **rewritten**, not appended
to.

**A red case carries no status.** Its title and description speak in the present
tense, as if the {{what}} already kept it — a new feature and a known defect alike.
That the {{what}} does not yet is what the red run says, and it goes in the hand-over,
where the red cases are listed with why each is red.

**A title says what happens and to whom, what goes wrong for them included.**
`An interrupted save leaves the administrator unaware` — not the branch of the
code that gets there, `Save aborted midway`, and not a number, `F2 Create refuses
an empty code`. It holds one rule, whole, promises no more than its `Then`s check,
and is true of every row of its table.

**Plain words, and one word for one thing** — the same in titles, steps and tables.
No idioms, no metaphors, no names from the code but one that is itself the contract
being approved. A title is rewritten if its translation into the language of the
requirements is not a sentence anyone would say.
A word of the {{what}}'s own is defined once, in the description of the `Feature` that
introduces it; a neighbour's is neither redefined nor replaced by a synonym.

**A case reads without the code.** A `Given` is a situation its reader recognises
— what someone did, what happened — not a branch of the code; an example is a
value or a text they would see, not a position or an id; and nothing in the case
needs knowing how the code works inside — the order it saves in, how one save
becomes several requests. Where the order is itself the rule, as with nothing leaving
the process inside a transaction, the `Rule` names it and the steps show it.

**A `Given` is the situation, a `When` is what happens, a `Then` is an observation.** A
step that only sets defaults for other steps is not a `Given`: it stays in the bindings.
The reader looks for what happened where it happens, not in a `Given` that already holds
the change. Reading a result is a `Then`, even through the {{what}} itself:
`tests/flows.yaml` counts only what a `When` sends.

**A case grows from simple to hard.** The plain case and its `Then` first, then the
complication — a repeat, another process — and its own `Then`. Several `When`/`Then`
blocks about one rule are the normal form and spare a case per complication;
identical steps in a row are fine once the plain case is checked above them.

**Only what decides the outcome shows.** A value that does not change it and that no
`Then` reads goes to a `Background` or the step's default, and a participant is named
by role once introduced. The reverse holds too: a step does not hide what decides the
outcome. A table's columns change the result and its rows read on their own: a matrix
of transitions, an answer read back, several records whose values all matter. When one
cell of it matters, say that fact in a step instead: `a contract paid until yesterday`.

**Name what the case excludes.** For a case of the form "X comes from Y", answer
which implementation would also pass it and should not. If one exists, the case
does not discriminate, and it needs variation — two facilities configured
differently rather than one. Put the answer in the delta list: a reviewer can
judge a named alternative, but not imagine the whole space of them. The variation
stays readable: the smallest example that shows the rule, not data the reader has to
solve. What such an example cannot exclude is named in the delta list as a gap.

**The negative is written, never implied.** "No reminder is sent" is a `Then`. An
assertion that exists only as a missing line cannot be reviewed.

**Fold duplicates along one axis only.** Two real cases that differ in one value are
one outline. Differing in the *setup* is not: merging leaves a column empty on most
rows, and a sparse column means two questions were forced into one table.

**An outline is one sentence with different values.** Read a row aloud with its title:
if that is not a sentence, it is not a row. A cell holds a value, or a phrase in the
place the sentence leaves for it, never a whole step. A row that alone needs a column
is a case of its own. One result column is a matrix; two that always change together
say one thing twice. A row is named through its title, and every column is substituted
somewhere: a column nobody reads is a placeholder someone forgot to wire.

**A case must not quietly re-assert a rule that has its own case.** Leaving a
field out of a request and then asserting the default it acquired tests the
default rule for a second time, in a case about something else — so when the
default changes, an unrelated case fails. Fill the cell.

**State the contract facts your cases lean on.** A case whose outcome depends on
something the contract does and no case says — that writing the same key again
edits rather than refuses, that a version is identified by a date — smuggles
that fact in. The reader cannot tell whether it is a rule or an accident, and
the reviewer cannot approve it. Either give it a case of its own or, when
another case already implies it, say it in the description of that case or of its
`Rule`.

**Assert the meaning, not the representation.** "The contract holds version 1"
pins how a link is stored; "the contract holds terms at 50 percent" pins what
has to survive, and it fails for the right reason when a later version is used
instead. Where both are available, the second is the case and the first is an
implementation detail the specification should not fix. What reaches a neighbour
is said the same way: the step's text says what the neighbour was told, and the
path, the event and the field are the binding's — unless the name is itself the
contract being approved, as an event's routing key or an error code another
service reads. A call to a neighbour is an outcome only where the effect lies with
that neighbour. A call that only feeds the {{what}}'s own decision is not: the case
says what that decision does for its user, and a case about the call alone has
nothing to say.

**A case holds the {{what}}'s rules, not a standard's.** What a standard defines —
a JWT's claims and signature, an HTTP method's semantics, a hash — is checked by
whoever implements it, and a case restating it tests the library. A case says what
the {{what}} decides with it: who is let in, for how long, what ends a session. How
the token is built belongs to the steps.

**Language.** Step text and case titles are English — a closed vocabulary. The
**reasoning** — descriptions and the delta list — is written in the language of the
requirements it is verified against, because a translation inside that comparison is
unaudited. Domain identifiers are never translated: they are the names on the wire.

### 2. Verification, then approval

**Say at the gate that the arithmetic was read, not run.** Every number in the cases
came from reading the code, and about one in a hundred will be wrong.

**Before handing over, someone with no context reads the file and retells it** — a
fresh session or a subagent, told to open nothing but where the project defines its
words. A retelling that says the {{what}} does what it does not, a title false by its
own steps and two places that contradict each other are fixed in the cases. A doubt is
listed at hand-over, not answered in the file: what it is worth is the human's call.

A human reads the cases, the source document and the delta list, then corrects or
approves. **No production code before approval.**

**Approval is the human switching from spec mode to redgreen, and it has not
happened yet.** Hand over what you have, say what is red and why, list what you need
decided, and wait.

### 3. Red-green

The specification is **frozen** at approval. Red-green has two halves in one
order: bind the cases and run them red, then change production code until they
are green.

{{binding}}

**A value one case depends on — a setting included — is set by that case's step,
never in the harness.** The harness is suite-wide and the platform's; putting a
case's decision there hides it from the reviewer.

**A field or type a step reads is shape, and the first production code you
write.** Add it empty, with no behaviour behind it, so the suite builds and the
case fails on the value rather than on the compiler. A shape another service
reads is a contract, and it is not yours: that is a return to spec mode.

**The red half is done when every case is red for its own reason.** A case red
because a step reads no column, or a suite that does not compile, is not that.
**No behaviour before that run** — it is the one moment every case is seen to
fail, and code written earlier takes it away.

#### Green

The loop changes production code. The cases stay as approved, and **a step
changed after the red run is named at hand-over**, with which cases were red and
which green before the change and after it.

Never reach green by weakening an assertion, widening a matcher, deleting a case
or relaxing a fixture. If the test cannot go green without touching the
specification, stop — that is the next section, not a licence.

**A case that was never red has not been shown to fail,** and the red count above
cannot notice it. That is a case whose fix landed with it — a value moved into
configuration, a condition widened — and every case rewritten with no defect behind it.
The proof is a change to the code, put in for one run and taken out again: the old
value back, each implementation the delta list names, then whatever breaks the rule of
a case not yet seen to fail, until every case has been. Exactly that case fails, for
its own reason. Say at the gate that you will and afterwards that you did, and name at
hand-over an implementation no case catches.

**Every production file in the diff is named by a case that went red.** Walk the diff
file by file at hand-over and say which case failed on it. A file with no answer is
either unnecessary or a behaviour change nothing pins.

**Hand over what each `Then` this task bound or changed asserts:** what it reads
from the step's text or table, and what it compares it with — the answer, a
read-back through the repository, a request that reached a neighbour. A `Then` that
reads less than its text says — the status and not the body, two columns of three —
passes on less than was approved.

**A step can be the thing that is wrong.** A failure not on the expected list is as
likely to be a step that does not send what the case says — a column not read, a value
formatted the wrong way — as a defect in the {{what}}. Read the failure before believing
it: a step defect that survives makes the case green while it tests nothing.

### Changing an approved specification

Only through the human who approved it, whatever the reason. **Never on your own
judgement.** Two triggers:

- an **internal logical conflict**: two approved cases cannot both hold. State
  the conflict and stop.
- an **external constraint**: the case needs data this {{what}} does not own, or
  an ordering the transport does not guarantee. State the constraint and stop.

The human decides what changes — the specification, the scope, or the design.

### What the merge request carries

The **specification**, as a diff, and a **link to the ticket** where the delta
list was argued.

**What the diff must not carry.** Files a reviewer can check without reading
anything else:

- **the platform's files** — {{platform-files}}. Their presence means the task
  granted itself the permissions it was working under.
- **any production file**, outside auto mode. Red-green is a separate hand-over
  with a diff of its own; production code arriving together with the cases means
  the code moved before anyone approved what it should do.
- **`CONTRIBUTING.md`.** It is the file that can switch this rule off, which
  makes it the most valuable one to edit and the one nobody should find edited
  here.
