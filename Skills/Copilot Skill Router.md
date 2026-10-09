# Copilot Skill Router

## Purpose

This file tells VS Code GitHub Copilot which repository skill files to read before performing different classes of work in the **AI-Assisted Pendulum Experiment Workbench** project.

Copilot must not assume that a skill is active merely because the file exists in the repository.

For every substantial task:

1. identify the task type,
2. read this router,
3. read the required skill files listed below,
4. state which skills are being applied,
5. summarize 2–4 task-relevant rules from those skills,
6. stop and report an error if a required skill file cannot be found or read.

Do not proceed using a guessed or remembered version of a skill.

---

## Repository Skill Location

All project skills are stored under:

`Skills/`

Current known skills include:

- `Skills/Project Manager & Systems Architect.md`
- `Skills/Pendulum Engineering Expert.md`
- `Skills/Physics & Experimental Validator.md`
- `Skills/GitHub Engineering Review.md`
- `Skills/GUI UX Expert.md`
- `Skills/Copilot Skill Router.md`

Additional specialist skills may be added later.

---

## Mandatory Skill Verification Header

At the beginning of every substantial Copilot response, include:

```text
Skills used for this task:
- <skill 1>
- <skill 2>

Relevant rules applied:
- <rule from skill>
- <rule from skill>
- <rule from skill>
```

If a required file cannot be read, respond:

```text
SKILL LOAD ERROR:
<file path> could not be found or read.

Task execution stopped.
```

Do not continue.

---

## Routing Rules

### 1. Project planning, milestones, scope, architecture, sequencing

Read:

- `Project Manager & Systems Architect.md`

Also read when engineering assumptions are involved:

- `Pendulum Engineering Expert.md`

Also read when repository state or commit review matters:

- `GitHub Engineering Review.md`

Typical tasks:

- Milestone planning
- Product scope
- Architecture
- Module ownership
- Interface planning
- Acceptance criteria
- Stage-gate decisions
- Risk management

---

### 2. Pendulum physics, modeling, resonance, excitation, equations

Read:

- `Pendulum Engineering Expert.md`

Also read when validating a claim:

- `Physics & Experimental Validator.md`

Typical tasks:

- Natural frequency
- Parametric resonance
- Excitation strategy
- Model equations
- Physical assumptions
- Simulation interpretation
- Experiment design

---

### 3. Validation of simulation or experiment

Read:

- `Physics & Experimental Validator.md`
- `Pendulum Engineering Expert.md`

Also read when reviewing committed code or data:

- `GitHub Engineering Review.md`

Typical tasks:

- Theory vs experiment
- Frequency validation
- Damping validation
- Data sanity checks
- Numerical convergence
- Parametric resonance claims
- Physics plausibility

---

### 4. GitHub code review, commit review, PR review, repository health

Read:

- `GitHub Engineering Review.md`

Also read when the change affects pendulum physics:

- `Pendulum Engineering Expert.md`

Also read when the change makes validation claims:

- `Physics & Experimental Validator.md`

Typical tasks:

- Review a pushed commit
- Review changed files
- Assess merge readiness
- Check repository consistency
- Review skill files
- Check reproducibility

---

### 5. GUI / UX / desktop interaction design

Read:

- `GUI UX Expert.md`
- `Project Manager & Systems Architect.md`

Also read when displaying validation results:

- `Physics & Experimental Validator.md`

Typical tasks:

- Screen layout
- Live telemetry dashboard
- User workflows
- Error states
- Plot presentation
- Validation presentation
- Experiment controls

---

### 6. STM32 / firmware work

Until a dedicated STM32 skill is created, read:

- `Project Manager & Systems Architect.md`
- `Pendulum Engineering Expert.md`
- `GitHub Engineering Review.md`

Also read:

- `Physics & Experimental Validator.md`

when the firmware affects physical interpretation, timing, sensing, or experimental validity.

Typical tasks:

- Serial protocol
- AS5600 acquisition
- Timing
- Telemetry
- Command handling
- Limit switches
- Homing
- Experiment control

---

### 7. Python desktop application work

Until a dedicated Python Application Engineer skill is created, read:

- `Project Manager & Systems Architect.md`
- `GUI UX Expert.md`
- `GitHub Engineering Review.md`

Also read:

- `Pendulum Engineering Expert.md`

when code handles physical parameters or model results.

Typical tasks:

- Application structure
- Serial communication
- Threading
- Data persistence
- Experiment management
- Module integration
- Plotting backend

---

### 8. Simscape integration

Until a dedicated Simscape Integration Expert skill is created, read:

- `Project Manager & Systems Architect.md`
- `Pendulum Engineering Expert.md`
- `Physics & Experimental Validator.md`
- `GitHub Engineering Review.md`

Typical tasks:

- Parameter mapping
- MATLAB/Simscape execution
- Import/export format
- Initial conditions
- Time alignment
- Model traceability

---

### 9. Data analysis / signal processing

Until a dedicated Data Analysis & Signal Processing skill is created, read:

- `Pendulum Engineering Expert.md`
- `Physics & Experimental Validator.md`
- `GitHub Engineering Review.md`

Typical tasks:

- Frequency estimation
- Damping
- Filtering
- FFT
- Phase
- RMSE
- Resampling
- Alignment
- Error metrics

---

### 10. Computer vision

Until a dedicated Computer Vision Expert skill is created, read:

- `Project Manager & Systems Architect.md`
- `Physics & Experimental Validator.md`
- `GitHub Engineering Review.md`

Also read:

- `Pendulum Engineering Expert.md`

when converting tracked geometry into pendulum angle.

Typical tasks:

- Video tracking
- Calibration
- Bob/marker detection
- Video-derived angle
- Synchronization
- Confidence estimation

---

## Multi-Skill Conflict Rule

If two skills appear to conflict:

1. do not silently choose one,
2. identify the conflict,
3. prefer:
   - explicit current repository evidence,
   - safety,
   - physical correctness,
   - validation requirements,
4. ask the engineer if the conflict affects architecture or scope.

---

## Stage-Gate Rule

If the task belongs to a defined milestone:

- read `Project Manager & Systems Architect.md`,
- identify the current milestone,
- work only within that milestone unless explicitly instructed otherwise,
- do not begin the next milestone automatically,
- end with the required stage-gate review when the milestone acceptance criteria are reached.

---

## Repository Source-of-Truth Rule

When repository evidence exists:

- inspect the actual file,
- do not rely on memory,
- do not assume local changes are pushed,
- do not claim a file was reviewed unless the repository copy was read.

---

## Framework-First Rule

During the current framework-building phase:

- unresolved hardware parameters may remain `TBD`,
- do not block architecture work unnecessarily,
- do not invent missing values,
- defer full parameter validation until the milestone where it matters.

---

## Skill Maintenance Rule

Whenever new specialist skills are added:

1. add them to the skill list,
2. update the relevant routing rules,
3. keep task categories aligned with actual project architecture.

The pushed GitHub version of this router is the authoritative version for Copilot routing.
